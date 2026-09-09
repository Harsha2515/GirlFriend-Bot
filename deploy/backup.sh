#!/usr/bin/env bash
#
# backup.sh -- Snapshot bot.db safely, keeping the last N copies.
#
# Run by cron nightly (installed by setup.sh), or by hand any time:
#     ./deploy/backup.sh
#
# Uses sqlite3's .backup rather than cp. The bot runs in WAL mode, so a plain
# cp can capture a torn database with committed transactions still sitting in
# the -wal file. .backup takes a proper consistent snapshot of a live DB.
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$APP_DIR/backups"
KEEP=14   # nightly backups to retain

# Honour DB_PATH from .env, defaulting to bot.db.
DB_PATH="bot.db"
if [ -f "$APP_DIR/.env" ]; then
    ENV_DB="$(grep -E '^DB_PATH=' "$APP_DIR/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
    [ -n "${ENV_DB:-}" ] && DB_PATH="$ENV_DB"
fi
case "$DB_PATH" in
    /*) DB_FILE="$DB_PATH" ;;
    *)  DB_FILE="$APP_DIR/$DB_PATH" ;;
esac

if [ ! -f "$DB_FILE" ]; then
    echo "$(date -Is) [skip] no database at $DB_FILE yet"
    exit 0
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/bot-$STAMP.db"

sqlite3 "$DB_FILE" ".backup '$TARGET'"

# Verify before trusting it, so a corrupt snapshot never displaces a good one.
if [ "$(sqlite3 "$TARGET" 'PRAGMA integrity_check;')" != "ok" ]; then
    echo "$(date -Is) [FAIL] integrity check failed, discarding $TARGET"
    rm -f "$TARGET"
    exit 1
fi

gzip -f "$TARGET"

# Rotate: keep the newest $KEEP, delete the rest.
ls -1t "$BACKUP_DIR"/bot-*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

SIZE="$(du -h "$TARGET.gz" | cut -f1)"
COUNT="$(ls -1 "$BACKUP_DIR"/bot-*.db.gz 2>/dev/null | wc -l)"
echo "$(date -Is) [ok] $TARGET.gz ($SIZE), $COUNT backup(s) retained"

# To restore:
#   sudo systemctl stop gfbot
#   gunzip -c backups/bot-YYYYMMDD-HHMMSS.db.gz > bot.db
#   sudo systemctl start gfbot
