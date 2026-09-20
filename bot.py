"""
Fenix announcements -> Discord webhook bot.
"""

import json
import os
import sys
import urllib.parse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


FENIX_URL = os.environ.get("FENIX_RSS_URL", "").strip()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
FENIX_SESSION_COOKIE = os.environ.get("FENIX_SESSION_COOKIE", "").strip()

BASE_URL = "https://fenix.tecnico.ulisboa.pt"
SEEN_FILE = "seen_announcements.json"
MAX_SEEN_TO_KEEP = 500


def parse_cookie_string(cookie_string):
    """Convert a browser cookie string into a requests cookie dictionary."""
    cookies = {}

    for part in cookie_string.split(";"):
        part = part.strip()

        if "=" not in part:
            continue

        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()

    return cookies


def load_seen():
    """Load announcement IDs that have already been posted."""
    if not os.path.exists(SEEN_FILE):
        return {"seen_guids": []}

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {"seen_guids": []}

        data.setdefault("seen_guids", [])
        return data

    except (json.JSONDecodeError, OSError):
        print(f"WARNING: Could not read {SEEN_FILE}; starting empty.")
        return {"seen_guids": []}


def save_seen(data):
    """Save posted announcement IDs."""
    with open(SEEN_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def create_http_session():
    """Create an HTTP session with retries for temporary Fenix failures."""
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
    return session


def fetch_page():
    """Fetch the Fenix page and return the HTTP response."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    session = create_http_session()

    response = session.get(
        FENIX_URL,
        headers=headers,
        cookies=parse_cookie_string(FENIX_SESSION_COOKIE),
        timeout=(15, 60),
        allow_redirects=True,
    )

    response.raise_for_status()
    return response


def is_login_page(response):
    """Detect redirects or HTML pages requiring authentication."""
    final_url = response.url.lower()
    html_content = response.text
    soup = BeautifulSoup(html_content, "html.parser")

    if any(marker in final_url for marker in ("login", "auth", "cas")):
        return True

    page_title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""

    if any(
        marker in page_title
        for marker in ("login", "sign in", "autenticação", "autenticacao")
    ):
        return True

    password_input = soup.find(
        "input",
        attrs={
            "type": lambda value: (
                isinstance(value, str) and value.lower() == "password"
            )
        },
    )

    return password_input is not None


def make_announcement(link_tag):
    """Build an announcement from a link element."""
    href = link_tag.get("href")
    title = link_tag.get_text(" ", strip=True)

    if not href or not title:
        return None

    link = urllib.parse.urljoin(BASE_URL, href)
    parsed_url = urllib.parse.urlparse(link)
    path = parsed_url.path.lower()

    # Ignore common navigation and authentication links.
    ignored_parts = (
        "/login",
        "/auth",
        "/logout",
        "/inscricoes",
        "/horarios",
        "/turmas",
        "/pessoas",
    )

    if any(part in path for part in ignored_parts):
        return None

    parent = link_tag.find_parent(["article", "li", "div"])
    description = ""

    if parent:
        parent_copy = BeautifulSoup(str(parent), "html.parser")

        # Remove the title from the description.
        for heading in parent_copy.find_all(
            ["h1", "h2", "h3", "h4", "h5", "h6"]
        ):
            if heading.get_text(" ", strip=True) == title:
                heading.decompose()

        description = parent_copy.get_text(
            separator="\n",
            strip=True,
        )

    return {
        "title": title,
        "link": link,
        "guid": link.rstrip("/").split("/")[-1],
        "description": description[:2000],
    }


def parse_announcements(html_content):
    """
    Parse announcements from Fenix.

    This supports the original linked-h5 layout and also scans other links
    when Fenix changes its HTML structure.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    container = soup.find(id="content-block") or soup.find(id="content") or soup

    links = container.find_all("a", href=True)
    items = []
    seen_links = set()

    for link_tag in links:
        href = link_tag.get("href", "").lower()
        link_text = link_tag.get_text(" ", strip=True)

        # Keep the original Fenix layout: linked h5 announcement headings.
        is_linked_h5 = link_tag.find_parent("h5") is not None

        # Support URLs used by other Fenix layouts.
        has_announcement_url = any(
            marker in href
            for marker in (
                "anuncio",
                "anuncios",
                "announcement",
                "announcements",
            )
        )

        # Support announcement cards whose surrounding text/class identifies them.
        parent = link_tag.find_parent(["article", "li", "div"])
        parent_text = parent.get_text(" ", strip=True).lower() if parent else ""
        parent_classes = " ".join(parent.get("class", [])).lower() if parent else ""

        has_announcement_container = (
            "announcement" in parent_classes
            or "anouncement" in parent_classes
            or "anúncio" in parent_text
            or "anuncio" in parent_text
        )

        if not (
            is_linked_h5
            or has_announcement_url
            or has_announcement_container
        ):
            continue

        item = make_announcement(link_tag)

        if item is None:
            continue

        if item["link"] in seen_links:
            continue

        seen_links.add(item["link"])
        items.append(item)

    if items:
        print(f"Parsed {len(items)} announcement(s) from Fenix.")
    else:
        page_title = (
            soup.title.get_text(" ", strip=True)
            if soup.title
            else "<no page title>"
        )

        print(
            "DEBUG: no announcements found; "
            f"page_title={page_title!r}; "
            f"links={len(links)}; "
            f"headings={len(container.find_all(['h2', 'h3', 'h4', 'h5']))}"
        )

        for link_tag in links[:20]:
            print(
                "DEBUG LINK: "
                f"text={link_tag.get_text(' ', strip=True)!r}; "
                f"href={link_tag.get('href')!r}"
            )

    return items


def send_to_discord(item):
    """Send one announcement to Discord."""
    embed = {
        "title": item["title"][:256] or "New Fenix announcement",
        "description": item["description"][:2048] or "Open the announcement for details.",
        "url": item["link"],
    }

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={
            "content": "📢 New Fenix announcement",
            "embeds": [embed],
        },
        timeout=15,
    )

    if response.status_code not in (200, 204):
        print(
            "Discord webhook failed: "
            f"HTTP {response.status_code} - {response.text[:500]}"
        )
        return False

    return True


def main():
    missing = []

    if not FENIX_URL:
        missing.append("FENIX_RSS_URL")

    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")

    if missing:
        print(f"ERROR: Missing required setting(s): {', '.join(missing)}")
        sys.exit(1)

    try:
        response = fetch_page()
    except requests.exceptions.RequestException as error:
        print(f"ERROR: Could not reach Fenix after retries: {error}")
        sys.exit(1)

    if is_login_page(response):
        print(
            "ERROR: Fenix returned a login page. "
            "FENIX_SESSION_COOKIE may be expired or invalid."
        )
        sys.exit(1)

    announcements = parse_announcements(response.text)

    if not announcements:
        print("Page fetched successfully, but zero announcements were found.")
        return

    seen_data = load_seen()
    seen_guids = set(seen_data.get("seen_guids", []))

    new_announcements = [
        item
        for item in announcements
        if item["guid"] not in seen_guids
    ]

    if not new_announcements:
        print(
            f"Checked {len(announcements)} announcement(s). "
            "Nothing new."
        )
        return

    new_announcements.reverse()

    print(
        f"Found {len(new_announcements)} new announcement(s). "
        "Posting to Discord..."
    )

    posted_guids = []

    for item in new_announcements:
        try:
            posted = send_to_discord(item)
        except requests.exceptions.RequestException as error:
            posted = False
            print(
                f"FAILED to post '{item['title']}' "
                f"because of a network error: {error}"
            )

        if posted:
            print(f"Posted: {item['title']}")
            posted_guids.append(item["guid"])
        else:
            print(f"FAILED to post '{item['title']}'.")

    if posted_guids:
        seen_guids.update(posted_guids)
        seen_data["seen_guids"] = list(seen_guids)[-MAX_SEEN_TO_KEEP:]
        save_seen(seen_data)


if __name__ == "__main__":
    main()
