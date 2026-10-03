#!/usr/bin/env sh
# Encrypted, off-server PostgreSQL backup for the production stack.
#
#   sh infra/scripts/backup.sh              # from the repository root on the VPS
#
# Requires: docker compose, age (https://age-encryption.org), rclone with a configured
# remote. Reads BACKUP_AGE_RECIPIENT, BACKUP_RCLONE_REMOTE and BACKUP_KEEP_DAYS from
# .env.production. The plaintext dump never touches disk: pg_dump streams into age.
# Schedule it daily (cron) — the pilot target is at most 24 hours of lost data.
set -eu

cd "$(dirname "$0")/../.."
ENV_FILE=${ENV_FILE:-.env.production}
COMPOSE="docker compose -f infra/compose/docker-compose.prod.yml --env-file $ENV_FILE"
# shellcheck disable=SC1090
. "./$ENV_FILE"

: "${BACKUP_AGE_RECIPIENT:?set BACKUP_AGE_RECIPIENT (an age public key) in $ENV_FILE}"
: "${BACKUP_RCLONE_REMOTE:?set BACKUP_RCLONE_REMOTE (e.g. offsite:opspilot-backups)}"
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p backups
OUT="backups/opspilot-$STAMP.dump.age"

# OPSPILOT_TAG only matters for app containers; postgres is addressed by service name.
export OPSPILOT_TAG=${OPSPILOT_TAG:-unused}
$COMPOSE exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom \
  | age -r "$BACKUP_AGE_RECIPIENT" > "$OUT"

# An empty or tiny file means the dump failed even if a pipe stage hid it.
SIZE=$(wc -c < "$OUT")
if [ "$SIZE" -lt 1024 ]; then
  echo "backup failed: $OUT is only $SIZE bytes" >&2
  exit 1
fi
sha256sum "$OUT" > "$OUT.sha256"

rclone copy "$OUT" "$BACKUP_RCLONE_REMOTE/"
rclone copy "$OUT.sha256" "$BACKUP_RCLONE_REMOTE/"
rclone delete --min-age "${KEEP_DAYS}d" "$BACKUP_RCLONE_REMOTE/"
find backups -name 'opspilot-*.dump.age*' -mtime +"$KEEP_DAYS" -delete

echo "backup ok: $OUT ($SIZE bytes) copied to $BACKUP_RCLONE_REMOTE"
