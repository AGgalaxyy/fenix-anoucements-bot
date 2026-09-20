# Local interactive-login test

This branch contains a **local-only** login test. It opens a visible browser and lets you log in to Fénix normally. It does not collect, store, or submit your Técnico password.

## Install

```powershell
python -m pip install requests beautifulsoup4 playwright
python -m playwright install chromium
```

## Run

Set the Discord webhook only in your local PowerShell session:

```powershell
$env:DISCORD_WEBHOOK_URL = "paste-your-webhook-here"
python local_login_bot.py
```

A browser opens. Log in to Fénix, complete any verification, open the announcements page, then return to the terminal and press Enter.

The browser session exists only in memory and is closed when the script ends. Do not put a username, password, cookie, or browser storage file in the repository.

This script is intentionally local-only; the GitHub Actions workflow remains cookie-based because a GitHub runner cannot safely perform your interactive personal login.
