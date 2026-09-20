"""
Fenix Announcement -> Discord bot (HTML Scraper Version)
"""

import json
import os
import sys
import urllib.parse

from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Settings
FENIX_SESSION_COOKIE = os.environ.get("FENIX_SESSION_COOKIE", "").strip()
FENIX_RSS_URL = os.environ.get("FENIX_RSS_URL", "").strip()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

SEEN_FILE = "seen_announcements.json"
MAX_SEEN_TO_KEEP = 500
BASE_URL = "https://fenix.tecnico.ulisboa.pt"


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return {"seen_guids": []}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("seen_guids", [])
        return data
    except (json.JSONDecodeError, OSError):
        print(f"WARNING: could not read {SEEN_FILE}, starting with an empty list.")
        return {"seen_guids": []}


def save_seen(data):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def parse_cookie_string(cookie_string):
    cookies = {}
    for part in cookie_string.split(";"):
        part = part.strip()
        if part and "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def fetch_page():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    response = session.get(
        FENIX_RSS_URL,
        headers=headers,
        cookies=parse_cookie_string(FENIX_SESSION_COOKIE),
        timeout=(15, 60),
        allow_redirects=True,
    )
    response.raise_for_status()
    return response


def looks_like_login_page(response):
    final_url = response.url.lower()
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if any(marker in final_url for marker in ("login", "auth", "cas")):
        return True
    if any(marker in title for marker in ("login", "sign in", "autenticação", "autenticacao")):
        return True
    return soup.find("input", attrs={"type": lambda value: value and value.lower() == "password"}) is not None


def announcement_from_link(link_tag):
    href = link_tag.get("href")
    title = link_tag.get_text(" ", strip=True)
    if not href or not title:
        return None

    link = urllib.parse.urljoin(BASE_URL, href)
    parsed = urllib.parse.urlparse(link)
    path = parsed.path.lower()

    # Avoid navigation, login, and generic page links. Announcement detail URLs
    # on Fenix normally remain below the course's /anuncios path.
    if any(part in path for part in ("/login", "/auth", "/logout", "/inscricoes", "/horarios")):
        return None
    if not any(part in path for part in ("/anuncio", "/announcement", "/announcements")):
        return None

    parent = link_tag.find_parent(["article", "li", "div"]) or link_tag.parent
    description = ""
    if parent:
        copy = BeautifulSoup(str(parent), "html.parser")
        for tag in copy.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            if tag.get_text(" ", strip=True) == title:
                tag.decompose()
        description = copy.get_text(separator="\n", strip=True)

    return {
        "title": title,
        "link": link,
        "guid": link.rstrip("/").split("/")[-1],
        "description": description[:2000],
    }


def parse_announcements(html_content):
    """Find announcement detail links instead of relying on one heading layout."""
    soup = BeautifulSoup(html_content, "html.parser")
    container = soup.find(id="content-block") or soup.find(id="content") or soup
    items = []
    seen_links = set()

    # Scan every link. This handles Fenix pages where announcements are rendered
    # as divs/list items rather than linked h5 headings.
    for link_tag in container.find_all("a", href=True):
        item = announcement_from_link(link_tag)
        if item and item["link"] not in seen_links:
            seen_links.add(item["link"])
            items.append(item)

    if not items:
        title = soup.title.get_text(" ", strip=True) if soup.title else "<none>"
        sample_links = [
            urllib.parse.urljoin(BASE_URL, a["href"])
            for a in container.find_all("a", href=True)[:10]
        ]
        print(
            "DEBUG: no announcement links matched; "
            f"page_title={title}; links={len(container.find_all('a', href=True))}; "
            f"sample_links={sample_links}"
        )
    else:
        print(f"Parsed {len(items)} announcement link(s) from Fenix.")

    return items


def send_to_discord(item):
    embed = {
        "title": item["title"][:256] or "New announcement",
        "description": item["description"][:2048],
    }
    if item["link"]:
        embed["url"] = item["link"]
    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": "📢 New Fenix announcement", "embeds": [embed]},
        timeout=15,
    )
    if response.status_code not in (200, 204):
        print(f"Discord webhook failed: HTTP {response.status_code} - {response.text[:500]}")
        return False
    return True


def main():
    missing = [name for name, value in (("FENIX_RSS_URL", FENIX_RSS_URL), ("DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK_URL)) if not value]
    if missing:
        print(f"ERROR: missing required setting(s): {', '.join(missing)}")
        sys.exit(1)

    try:
        response = fetch_page()
    except requests.exceptions.RequestException as exc:
        print(f"ERROR: could not reach Fenix after retries: {exc}")
        sys.exit(1)

    if looks_like_login_page(response):
        print("ERROR: Fenix returned a login page. Your FENIX_SESSION_COOKIE may be expired or invalid.")
        sys.exit(1)

    items = parse_announcements(response.text)
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
    posted = []
    for item in new_items:
        try:
            ok = send_to_discord(item)
        except requests.exceptions.RequestException as exc:
            ok = False
            print(f"FAILED (network error) to post '{item['title']}': {exc}")
        if ok:
            print(f"Posted: {item['title']}")
            posted.append(item["guid"])
        else:
            print(f"FAILED to post '{item['title']}' - will retry next run.")

    if posted:
        seen_guids.update(posted)
        seen_data["seen_guids"] = list(seen_guids)[-MAX_SEEN_TO_KEEP:]
        save_seen(seen_data)


if __name__ == "__main__":
    main()
