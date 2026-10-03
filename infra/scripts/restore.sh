#!/usr/bin/env sh
# Restore an encrypted backup into an ISOLATED database and validate it.
#
#   sh infra/scripts/restore.sh backups/opspilot-20261003T020000Z.dump.age ~/.config/age/key.txt
#
# Never restores over the live database. It creates opspilot_restore_<stamp> inside
# the running postgres container, restores into it, and reports row counts and the
# migration head next to the live database's, so a person can decide what to do.
# Promotion to live is a deliberate, separate step (docs/runbooks/DEPLOY.md).
set -eu

cd "$(dirname "$0")/../.."
BACKUP=${1:?usage: restore.sh <backup.dump.age> <age-identity-file>}
IDENTITY=${2:?usage: restore.sh <backup.dump.age> <age-identity-file>}
ENV_FILE=${ENV_FILE:-.env.production}
COMPOSE="docker compose -f infra/compose/docker-compose.prod.yml --env-file $ENV_FILE"
# shellcheck disable=SC1090
. "./$ENV_FILE"
export OPSPILOT_TAG=${OPSPILOT_TAG:-unused}

if [ -f "$BACKUP.sha256" ]; then
  sha256sum -c "$BACKUP.sha256"
fi

TARGET="opspilot_restore_$(date -u +%Y%m%d%H%M%S)"
PSQL="$COMPOSE exec -T postgres psql -v ON_ERROR_STOP=1 -U $POSTGRES_USER"

$PSQL -d postgres -c "CREATE DATABASE \"$TARGET\" TEMPLATE template0"
$PSQL -d "$TARGET" -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS pgcrypto;"
age -d -i "$IDENTITY" "$BACKUP" \
  | $COMPOSE exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$TARGET" --no-owner --exit-on-error

COUNTS="select 'alembic', (select version_num from alembic_version)
  union all select 'organizations', count(*)::text from organizations
  union all select 'memberships', count(*)::text from memberships
  union all select 'orders', count(*)::text from orders
  union all select 'agent_runs', count(*)::text from agent_runs
  union all select 'proposed_actions', count(*)::text from proposed_actions
  union all select 'ops_tasks', count(*)::text from ops_tasks
  union all select 'documents', count(*)::text from documents"

echo "restored into $TARGET:"
$PSQL -d "$TARGET" -At -F ' = ' -c "$COUNTS"
echo "live database $POSTGRES_DB:"
$PSQL -d "$POSTGRES_DB" -At -F ' = ' -c "$COUNTS"
echo "validate, then drop with: $PSQL -d postgres -c 'DROP DATABASE \"$TARGET\"'"
