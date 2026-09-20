# Local interactive login setup

This branch is prepared for a local-only test. It does not automate or store your Técnico password.

## One-time setup (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-local.txt
python -m playwright install chromium
Copy-Item .env.example .env.local
notepad .env.local
```

Put your Discord webhook in `.env.local`. Leave the Fénix URL unchanged unless you need a different course.

## Run

Set the values from `.env.local` in the current PowerShell window:

```powershell
Get-Content .env.local | ForEach-Object {
    if ($_ -match '^\s*([^#=][^=]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
    }
}
python local_login_bot.py
```

A visible Chromium window opens. Log in to Fénix normally in that window, complete any 2FA if requested, open the announcements page, then return to the terminal and press Enter. New announcements are sent to Discord.

The browser session is held only in memory and is closed when the script exits. Do not create or commit a password file, cookie, `fenix-auth.json`, or browser storage state. The GitHub Actions workflow remains separate and uses its existing session-cookie secret.

If PowerShell blocks activation, run the commands without activating the virtual environment by replacing `python` with `.venv\Scripts\python.exe`.
