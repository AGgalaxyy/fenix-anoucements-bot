"""
Fenix Announcement -> Discord bot (HTML Scraper Version)
"""

import html
import json
import os
import re
import sys
import urllib.parse
from bs4 import BeautifulSoup
import requests

# Settings
FENIX_SESSION_COOKIE = os.environ.get("FENIX_SESSION_COOKIE", "").strip()
FENIX_RSS_URL = os.environ.get("FENIX_RSS_URL", "").strip()  # Use full Announcements page URL here
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

SEEN_FILE = "seen_announcements.json"
MAX_SEEN_TO_KEEP = 500


def load_seen():
    """Read the list of announcement IDs already posted."""
    if not os.path.exists(SEEN_FILE):
        return {"seen_guids": []}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "seen_guids" not in data:
                data["seen_guids"] = []
            return data
    except (json.JSONDecodeError, OSError):
        print(f"WARNING: could not read {SEEN_FILE}, starting with an empty list.")
        return {"seen_guids": []}


def save_seen(data):
    """Save updated list of announcement IDs."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def parse_cookie_string(cookie_string):
    """Parse cookie header into key-value pairs."""
    cookies = {}
    for part in cookie_string.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies


def fetch_page():
    """Download HTML content from Fenix course announcements page."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    cookies = parse_cookie_string(FENIX_SESSION_COOKIE) if FENIX_SESSION_COOKIE else {}

    response = requests.get(FENIX_RSS_URL, headers=headers, cookies=cookies, timeout=30)
    response.raise_for_status()
    return response.text


def looks_like_login_page(html_content):
    """Check if session cookie expired and Fenix returned a login prompt."""
    sample = html_content[:2000].lower()
    if "login" in sample and ("cas" in sample or "fenix" in sample) and "password" in sample:
        return True
    return False


def parse_announcements(html_content):
    """Parse DOM elements from the announcements block."""
    soup = BeautifulSoup(html_content, "html.parser")
    container = soup.find(id="content-block") or soup
    items = []

    for h5 in container.find_all("h5"):
        a_tag = h5.find("a")
        if not a_tag or not a_tag.get("href"):
            continue

        title = a_tag.get_text(strip=True)
        link = urllib.parse.urljoin("https://fenix.tecnico.ulisboa.pt", a_tag["href"])
        
        # Deduplication GUID using the unique URL slug
        guid = link.rstrip("/").split("/")[-1]

        parent_div = h5.find_parent("div")
        description = ""

        if parent_div:
            div_copy = BeautifulSoup(str(parent_div), "html.parser")
            
            # Remove title (h5) and metadata date/author paragraph (<p class="small">)
            for tag in div_copy.find_all(["h5", "p"], class_=["small"]):
                tag.decompose()
            
            # Remove duplicate title header inside body if present
            for h2 in div_copy.find_all("h2"):
                if h2.get_text(strip=True) == title:
                    h2.decompose()

            description = div_copy.get_text(separator="\n", strip=True)

        items.append(
            {
                "title": title,
                "link": link,
                "guid": guid,
                "description": description[:2000],
            }
        )

    return items


def send_to_discord(item):
    """Post single announcement embed to Discord webhook."""
    embed = {
        "title": item["title"][:256] or "New announcement",
        "description": item["description"][:2048],
    }
    if item["link"]:
        embed["url"] = item["link"]

    payload = {
        "content": "📢 New Fenix announcement",
        "embeds": [embed],
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
    return response.status_code in (200, 204)


def main():
    missing = []
    if not FENIX_RSS_URL:
        missing.append("FENIX_RSS_URL")
    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")
    if missing:
        print(f"ERROR: missing required setting(s): {', '.join(missing)}")
        sys.exit(1)

    try:
        html_content = fetch_page()
    except requests.exceptions.RequestException as e:
        print(f"ERROR: could not reach Fenix: {e}")
        sys.exit(1)

    if looks_like_login_page(html_content):
        print(
            "ERROR: Fenix sent back a login page. "
            "Your FENIX_SESSION_COOKIE has expired. Update it in Secrets."
        )
        sys.exit(1)

    items = parse_announcements(html_content)

    if not items:
        print("Page fetched successfully, but zero announcements were found.")
        return

    seen_data = load_seen()
    seen_guids = set(seen_data.get("seen_guids", []))
    new_items = [item for item in items if item["guid"] not in seen_guids]

    if not new_items:
        print(f"Checked {len(items)} announcement(s). Nothing new.")
        return

    new_items.reverse()

    print(f"Found {len(new_items)} new announcement(s). Posting to Discord...")
    newly_posted_guids = []
    for item in new_items:
        try:
            ok = send_to_discord(item)
        except requests.exceptions.RequestException as e:
            ok = False
            print(f"  FAILED (network error) to post '{item['title']}': {e}")

        if ok:
            print(f"  Posted: {item['title']}")
            newly_posted_guids.append(item["guid"])
        else:
            print(f"  FAILED to post '{item['title']}' - will retry next run.")

    if newly_posted_guids:
        seen_guids.update(newly_posted_guids)
        seen_data["seen_guids"] = list(seen_guids)[-MAX_SEEN_TO_KEEP:]
        save_seen(seen_data)


if __name__ == "__main__":
    main()
