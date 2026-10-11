#!/usr/bin/env bash
# One-shot installer for a fresh Ubuntu/Debian VPS (run as root):  bash deploy/install.sh
# Installs SelfTBC as a systemd service on port 8080 (or put Caddy in front: see README).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root (sudo -i)"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
apt-get update -y && apt-get install -y python3 python3-venv python3-pip rsync
id selftbc >/dev/null 2>&1 || useradd -r -m -d /opt/selftbc -s /usr/sbin/nologin selftbc
mkdir -p /opt/selftbc/data
rsync -a --exclude data --exclude deploy --exclude tests --exclude .git "$SRC/" /opt/selftbc/
python3 -m venv /opt/selftbc/venv
/opt/selftbc/venv/bin/pip install -q -r /opt/selftbc/requirements.txt
KEYFILE=/opt/selftbc/.key
[ -f "$KEYFILE" ] || head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$KEYFILE"
chown -R selftbc:selftbc /opt/selftbc && chmod 600 "$KEYFILE" && chmod 700 /opt/selftbc/data
cat > /etc/systemd/system/selftbc.service <<UNIT
[Unit]
Description=SelfTBC
After=network-online.target
[Service]
User=selftbc
WorkingDirectory=/opt/selftbc
Environment=SELFTBC_DB=/opt/selftbc/data/selftbc.db
Environment=PORT=8080
EnvironmentFile=/opt/selftbc/.env.runtime
ExecStart=/opt/selftbc/venv/bin/python -m selftbc --host 0.0.0.0
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
echo "SELFTBC_KEY=$(cat $KEYFILE)" > /opt/selftbc/.env.runtime
# optional telegram auto-backup: add SELFTBC_BACKUP_CHAT=<your id> and SELFTBC_BACKUP_HOURS=6 to /opt/selftbc/.env.runtime
chown selftbc /opt/selftbc/.env.runtime && chmod 600 /opt/selftbc/.env.runtime
systemctl daemon-reload && systemctl enable --now selftbc
IP=$(curl -s https://api.ipify.org || hostname -I | awk '{print $1}')
echo; echo "✅ SelfTBC running:  http://$IP:8080"; echo "🔑 Dashboard key: $(cat $KEYFILE)"
echo "Open port 8080 in your provider firewall, or use Caddy for HTTPS (README)."
