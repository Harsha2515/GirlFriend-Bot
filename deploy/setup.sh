#!/usr/bin/env bash
#
# setup.sh -- Provision the GirlFriend Bot on a fresh Ubuntu VM.
#
# Written for Oracle Cloud Always Free (works on any Ubuntu 22.04/24.04 box,
# ARM or x86). Safe to re-run: every step checks before it acts, so you can
# use this to redeploy after pulling new code.
#
#   git clone <your repo> ~/GirlFriend-Bot
#   cd ~/GirlFriend-Bot
#   bash deploy/setup.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(id -un)"
SERVICE_NAME="gfbot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
MIN_PY_MINOR=10   # the code uses 3.10+ syntax (X | None, zoneinfo)

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$1"; }
die()  { printf '\033[1;31m[x] %s\033[0m\n' "$1" >&2; exit 1; }

[ "$APP_USER" = "root" ] && die "Run this as your normal user (e.g. ubuntu), not root."

say "Deploying from: $APP_DIR  (as user: $APP_USER)"

# ── 1. System packages ────────────────────────────────────────────────────────
say "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip sqlite3 tzdata

# ── 2. Python version ─────────────────────────────────────────────────────────
PY=python3
PY_MINOR="$($PY -c 'import sys; print(sys.version_info.minor)')"

if [ "$PY_MINOR" -lt "$MIN_PY_MINOR" ]; then
    warn "System python3 is 3.$PY_MINOR; this bot needs 3.$MIN_PY_MINOR+. Installing 3.11..."
    sudo apt-get install -y -qq software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3.11 python3.11-venv
    PY=python3.11
fi
say "Using $($PY --version)"

# ── 3. Swap (the Always Free micro VM has only 1 GB of RAM) ───────────────────
TOTAL_MB="$(free -m | awk '/^Mem:/{print $2}')"
if [ "$TOTAL_MB" -lt 1500 ] && [ ! -f /swapfile ]; then
    say "Only ${TOTAL_MB}MB RAM detected -- adding a 2GB swapfile"
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || \
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# ── 4. Virtualenv + dependencies ──────────────────────────────────────────────
say "Setting up the virtualenv"
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    "$PY" -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ── 5. Secrets ────────────────────────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    say "Creating .env -- you must fill this in"
    cat > "$APP_DIR/.env" <<'ENVEOF'
TELEGRAM_BOT_TOKEN=
GEMINI_API_KEY=
DB_PATH=bot.db
TIMEZONE_DEFAULT=Asia/Kolkata
ENVEOF
    chmod 600 "$APP_DIR/.env"
    warn "Edit $APP_DIR/.env with your two keys, then re-run this script."
    exit 0
fi
chmod 600 "$APP_DIR/.env"

if ! grep -q '^TELEGRAM_BOT_TOKEN=.\+' "$APP_DIR/.env" \
|| ! grep -q '^GEMINI_API_KEY=.\+'     "$APP_DIR/.env"; then
    die ".env is missing TELEGRAM_BOT_TOKEN or GEMINI_API_KEY. Fill it in and re-run."
fi

# ── 6. Pre-flight check ───────────────────────────────────────────────────────
say "Running validate.py"
( cd "$APP_DIR" && ./venv/bin/python validate.py ) || die "validate.py failed -- not installing the service."

# ── 7. systemd service ────────────────────────────────────────────────────────
say "Installing the systemd service"
sed -e "s|__APP_DIR__|$APP_DIR|g" \
    -e "s|__USER__|$APP_USER|g" \
    "$APP_DIR/deploy/gfbot.service" | sudo tee "$SERVICE_FILE" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" >/dev/null
sudo systemctl restart "$SERVICE_NAME"

# ── 8. Nightly database backup ────────────────────────────────────────────────
say "Scheduling the nightly backup"
CRON_LINE="0 3 * * * $APP_DIR/deploy/backup.sh >> $APP_DIR/backups/backup.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'deploy/backup.sh' ; echo "$CRON_LINE" ) | crontab -
chmod +x "$APP_DIR/deploy/backup.sh"

# ── 9. Report ─────────────────────────────────────────────────────────────────
sleep 4
say "Status"
sudo systemctl status "$SERVICE_NAME" --no-pager --lines=15 || true

cat <<EOF

$(printf '\033[1;32m')Deployed.$(printf '\033[0m')

  Live logs      journalctl -u $SERVICE_NAME -f
  Restart        sudo systemctl restart $SERVICE_NAME
  Stop           sudo systemctl stop $SERVICE_NAME
  Update code    cd $APP_DIR && git pull && bash deploy/setup.sh
  Back up now    $APP_DIR/deploy/backup.sh

The bot polls Telegram over outbound HTTPS only -- no inbound ports, so there
is nothing to open in your Oracle security list or in ufw/iptables.
EOF
