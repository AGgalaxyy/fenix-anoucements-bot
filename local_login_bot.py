"""Run the Fénix announcement bot locally using an interactive browser login.

This intentionally does not accept or store a username/password. Playwright opens
Fénix in a visible browser; log in normally, including any required 2FA, and the
script reuses that in-memory browser session to read announcements.
"""

import json
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


FENIX_URL = os.environ.get(
    "FENIX_URL",
    "https://fenix.tecnico.ulisboa.pt/disciplinas/IEECom/2026-2027/1-semestre/anuncios",
)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
SEEN_FILE = Path("seen_announcements.json")


def load_seen():
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return set(data.get("seen_guids", []))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps({"seen_guids": sorted(seen)[-500:]}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def parse_announcements(html):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find(id="content-block") or soup.find(id="content") or soup
    items = []
    seen_links = set()

    for link_tag in container.find_all("a", href=True):
        href = link_tag["href"]
        title = link_tag.get_text(" ", strip=True)
        is_heading_link = link_tag.find_parent(["h4", "h5", "h6"]) is not None
        is_announcement_link = any(
            word in href.lower()
            for word in ("anuncio", "anuncios", "announcement", "announcements")
        )

        if not title or not (is_heading_link or is_announcement_link):
            continue

        link = "https://fenix.tecnico.ulisboa.pt" + href if href.startswith("/") else href
        if "/login" in link.lower() or link in seen_links:
            continue

        parent = link_tag.find_parent(["article", "li", "div"])
        description = parent.get_text("\n", strip=True) if parent else ""
        guid = link.rstrip("/").split("/")[-1] or link
        seen_links.add(link)
        items.append({"guid": guid, "title": title, "link": link, "description": description[:2000]})

    return items


def post_to_discord(item):
    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={
            "content": "📢 New Fénix announcement",
            "embeds": [{
                "title": item["title"][:256],
                "description": item["description"] or "Open the announcement for details.",
                "url": item["link"],
            }],
        },
        timeout=15,
    )
    response.raise_for_status()


def main():
    if not DISCORD_WEBHOOK_URL:
        print("ERROR: set DISCORD_WEBHOOK_URL before running.", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(FENIX_URL, wait_until="domcontentloaded")

        print("A browser window is open. Log in to Fénix there if requested.")
        input("When the announcements are visible, press Enter here... ")

        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeoutError:
            pass

        items = parse_announcements(page.content())
        if not items:
            print("No announcements found. Confirm that the logged-in page is visible.")
            browser.close()
            return

        seen = load_seen()
        new_items = [item for item in items if item["guid"] not in seen]
        print(f"Found {len(new_items)} new announcement(s).")

        for item in reversed(new_items):
            try:
                post_to_discord(item)
                print(f"Posted: {item['title']}")
                seen.add(item["guid"])
            except requests.RequestException as error:
                print(f"Failed to post {item['title']}: {error}", file=sys.stderr)

        save_seen(seen)
        browser.close()


if __name__ == "__main__":
    main()
