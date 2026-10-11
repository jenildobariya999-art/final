# SelfTBC — self-hosted Telebot Creator (TPY runtime)

Runs your TBC bots on your own VPS. Same command model and TPY language
(`bot`, `Bot`, `User`, `libs`, `msg`, `params`, `options`, `~ @ ! *` specials, `.env`,
`handleNextCommand`, `runCommandAfter`, `raise ReturnCommand`, 3-hop `runCommand` chain limit...).
Pure Python + `requests`, SQLite storage, web dashboard.

## Install (VPS, Ubuntu)
    sudo useradd -r -m -d /opt/selftbc selftbc
    sudo cp -r . /opt/selftbc && cd /opt/selftbc
    python3 -m venv venv && venv/bin/pip install -r requirements.txt
    sudo mkdir -p data && sudo chown selftbc data
    # edit SELFTBC_KEY in selftbc.service, then:
    sudo cp selftbc.service /etc/systemd/system/ && sudo systemctl enable --now selftbc
Put Caddy/nginx with HTTPS in front of 127.0.0.1:8080. Or: `docker compose up -d`.

## Migrate from TBC
1. In the TBC app: Bot -> Commands -> export (`=== name ===` text) and Manage -> Export users (JSON/CSV).
2. Dashboard: Add bot (token) -> Commands -> Import (that text file) -> Users -> Import file.
   Stop the bot on TBC first (only one poller per token). Bot data / user data import via Data -> Import.
3. Start. Check Errors tab.

## What works / what doesn't
Works: all special commands, user/bot data, scheduler, broadcasts, import/export (commands, users, data, full backup),
search & replace across code, error log, test runner, Bot API via `bot.<anyMethod>` (camelCase or snake_case).
Not included (TBC-hosted services): libs.Coinbase / Coinpayments / Polygon / web3lib / TON / tbcads / auto-translate,
Mini App hosting, `Bot.Transfer`, points/`left_points` (fixed huge number), MTProto accelerated mode.
Add any of those as classes in `runtime.make_libs`.

## Security
The TPY sandbox (AST checks + whitelisted builtins) is for YOUR OWN code. It is not a hardened jail:
do not let untrusted people write commands. Always set `SELFTBC_KEY`; bot tokens are stored plaintext in SQLite
(`chmod 600` the data dir).

## Free hosting for testing (no credit card)
See `deploy/FREE_HOSTING.md`. TL;DR: Hugging Face Space (Docker) or Render free web service,
kept awake with UptimeRobot pinging `/api/ping`; set `SELFTBC_BACKUP_CHAT` so you get Telegram backups
(free disks are temporary). For real use: a VPS + `bash deploy/install.sh`.
