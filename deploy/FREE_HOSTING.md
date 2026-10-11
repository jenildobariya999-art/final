# Trying SelfTBC for free (no credit card)

Free tiers change often — check each site's current terms before relying on them.

## Option A — Hugging Face Space (Docker)  ← easiest
1. Sign up at huggingface.co (email only).  New Space -> SDK: **Docker** -> Blank -> **Private**.
2. Upload the project files (web "Files" tab or git push) so the Space root contains `Dockerfile`, `requirements.txt`, `selftbc/`.
   Replace the Space's `README.md` with the contents of `deploy/HF_SPACE_README.md`.
3. Space -> Settings -> **Variables and secrets** -> add secrets:
   `SELFTBC_KEY` (long random), `SELFTBC_BACKUP_CHAT` (your Telegram numeric id), `SELFTBC_BACKUP_HOURS` = 6
4. Wait for build. Open the Space URL (private Spaces need you logged in to HF). Enter the key -> Add bot.
5. Keep it awake: free Spaces sleep when idle -> create a free UptimeRobot monitor on `<space-url>/api/ping` every 5 min
   (a private Space blocks outside pings: either make the Space public — the dashboard is still key-protected — or skip pinging and use it only for tests).

## Option B — Render free web service
1. Push the project to a GitHub repo. render.com -> New -> Blueprint -> select repo (uses `deploy/render.yaml`). Free web services do not ask for a card.
2. Set `SELFTBC_BACKUP_CHAT`. Open the URL, log in with generated `SELFTBC_KEY` (Render -> Environment).
3. Free services sleep after ~15 min without inbound HTTP -> UptimeRobot ping `/api/ping` every 5 min.

## Caveats (important)
* Free disks are **temporary**: the SQLite DB is lost on restart/redeploy. The auto-backup sends you a JSON file in Telegram;
  restore with Backup -> Restore backup. Fine for testing, NOT for 77k users.
* Small RAM/CPU, shared IPs, and hosts may forbid always-on bots via pings. Treat it as a trial.
* Don't run all 247 bots there. Test 1-2 bots first.

## When it works for you -> real VPS
Any cheap VPS (Ubuntu 22.04/24.04, 1 GB RAM is enough to start):  `unzip selftbc.zip && cd selftbc && sudo bash deploy/install.sh`
It prints your URL and dashboard key. For HTTPS install Caddy and use: `your.domain { reverse_proxy 127.0.0.1:8080 }`
(change the service port/host to 127.0.0.1 in that case).
