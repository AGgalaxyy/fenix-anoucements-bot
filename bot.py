"""
Fenix (IST) announcement -> Discord webhook bot.

Reads the course's announcement RSS feed (authenticated, since this course's
announcements are only visible to logged-in "Utilizadores Autenticados"),
compares against a list of previously-seen announcement GUIDs, and posts any
new ones to a Discord webhook.

Auth strategy: session cookie, not scripted username/password login.
Técnico now routes logins through "Fenix Connect", which supports 2FA and
third-party identity providers -- a scripted form POST of username/password
is fragile and may simply not work depending on your account's auth method.
A copied session cookie is more reliable, at the cost of needing to be
refreshed periodically (see README for how).
"""

import json
import os
import sys
import xml.etree.ElementTree as ET

import requests

COURSE_RSS_URL = "https://fenix.tecnico.ulisboa.pt/disciplinas/IEECom/2026-2027/1-semestre/rss/announcement"
COURSE_PAGE_URL = "https://fenix.tecnico.ulisboa.pt/disciplinas/IEECom/2026-2027/1-semestre/anuncios"

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SESSION_COOKIE = os.getenv("FENIX_SESSION_COOKIE")  # e.g. "JSESSIONID=abc123..."
STORAGE_FILE = "seen_announcements.json"
MAX_STORED_IDS = 100  # cap so the state file doesn't grow forever


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (fenix-announcement-bot)"})

    if not SESSION_COOKIE:
        print("ERROR: FENIX_SESSION_COOKIE is not set.", file=sys.stderr)
        sys.exit(1)

    # Accept either "name=value" or just "value" (assumes JSESSIONID).
    if "=" in SESSION_COOKIE:
        name, _, value = SESSION_COOKIE.partition("=")
    else:
        name, value = "JSESSIONID", SESSION_COOKIE

    session.cookies.set(name.strip(), value.strip(), domain="fenix.tecnico.ulisboa.pt")
    return session


def fetch_announcements(session: requests.Session):
    response = session.get(COURSE_RSS_URL, timeout=30)
    response.raise_for_status()

    # If the cookie is dead/expired, Fenix typically serves an HTML login
    # page instead of RSS XML. Catch that explicitly instead of failing
    # deep inside the XML parser with a confusing error.
    content_type = response.headers.get("Content-Type", "")
    if "xml" not in content_type and "<rss" not in response.text[:200].lower():
        print(
            "ERROR: Did not get an RSS/XML response -- the session cookie is "
            "likely expired or invalid. Refresh FENIX_SESSION_COOKIE.",
            file=sys.stderr,
        )
        sys.exit(1)

    root = ET.fromstring(response.text)
    items = root.findall("./channel/item")

    announcements = []
    for item in items:
        guid_elem = item.find("guid")
        title_elem = item.find("title")
        link_elem = item.find("link")
        desc_elem = item.find("description")
        pubdate_elem = item.find("pubDate")

        guid = (guid_elem.text or "").strip() if guid_elem is not None else None
        title = (title_elem.text or "(no title)").strip() if title_elem is not None else "(no title)"
        link = (link_elem.text or "").strip() if link_elem is not None else COURSE_PAGE_URL
        description = (desc_elem.text or "").strip() if desc_elem is not None else ""
        pub_date = (pubdate_elem.text or "").strip() if pubdate_elem is not None else ""

        if not guid:
            # Fall back to link as a de-dup key if guid is somehow missing.
            guid = link or title

        announcements.append(
            {
                "id": guid,
                "title": title,
                "link": link,
                "body": description,
                "pub_date": pub_date,
            }
        )

    return announcements


def strip_html(text: str) -> str:
    # RSS <description> content is HTML-escaped HTML. Very small, dependency-free
    # tag stripper -- good enough for Discord embed text, not a full HTML parser.
    import re

    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def send_to_discord(announcement: dict) -> bool:
    body = strip_html(announcement["body"])[:1024] or "(no content)"

    embed = {
        "title": f"\U0001f4e2 New Announcement: {announcement['title']}"[:256],
        "url": announcement["link"] or COURSE_PAGE_URL,
        "color": 3447003,
        "description": body,
        "footer": {"text": "F\u00e9nix IEECom Updates"},
    }
    if announcement["pub_date"]:
        embed["fields"] = [{"name": "Posted", "value": announcement["pub_date"], "inline": False}]

    payload = {"embeds": [embed]}

    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"ERROR: failed to post to Discord for '{announcement['title']}': {exc}", file=sys.stderr)
        return False


def load_seen_ids() -> list:
    try:
        with open(STORAGE_FILE, "r") as f:
            data = json.load(f)
            return data.get("seen_ids", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_seen_ids(seen_ids: list) -> None:
    trimmed = seen_ids[-MAX_STORED_IDS:]
    with open(STORAGE_FILE, "w") as f:
        json.dump({"seen_ids": trimmed}, f, indent=2)


def main():
    if not WEBHOOK_URL:
        print("ERROR: WEBHOOK_URL is not set.", file=sys.stderr)
        sys.exit(1)

    session = build_session()
    announcements = fetch_announcements(session)

    if not announcements:
        print("No announcements found in feed.")
        return

    seen_ids = load_seen_ids()
    seen_set = set(seen_ids)

    # RSS feeds are newest-first; reverse so we post in chronological order
    # if several are new at once (and so Discord message order matches
    # actual posting order).
    new_announcements = [a for a in announcements if a["id"] not in seen_set]
    new_announcements.reverse()

    if not new_announcements:
        print("No new announcements.")
        return

    print(f"Found {len(new_announcements)} new announcement(s).")

    posted_ids = []
    for announcement in new_announcements:
        print(f" - Posting: {announcement['title']}")
        if send_to_discord(announcement):
            posted_ids.append(announcement["id"])
        # If a post fails, we deliberately don't mark it seen, so it's
        # retried on the next run instead of being silently dropped.

    if posted_ids:
        save_seen_ids(seen_ids + posted_ids)


if __name__ == "__main__":
    main()
