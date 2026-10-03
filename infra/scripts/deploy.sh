#!/usr/bin/env sh
# Deploy (or roll back to) a tagged release on the VPS.
#
#   sh infra/scripts/deploy.sh 2026-10-03.1        # deploy this tag
#   sh infra/scripts/deploy.sh "$(cat .deploy/previous)"   # roll back
#
# Pulls the tag, applies migrations (backward compatible for one release, so the
# previous image still works afterwards), restarts, and waits for /health/ready.
# Records the current and previous tags under .deploy/ for a one-line rollback.
set -eu

cd "$(dirname "$0")/../.."
TAG=${1:?usage: deploy.sh <image-tag>}
ENV_FILE=${ENV_FILE:-.env.production}
export OPSPILOT_TAG="$TAG"
COMPOSE="docker compose -f infra/compose/docker-compose.prod.yml --env-file $ENV_FILE"

$COMPOSE pull
$COMPOSE run --rm migrate
$COMPOSE up -d --remove-orphans

i=0
READY="import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"
until $COMPOSE exec -T api python -c "$READY" > /dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 45 ]; then
    echo "release $TAG is not healthy after 90 s" >&2
    if [ -f .deploy/current ]; then
      echo "roll back with: sh infra/scripts/deploy.sh $(cat .deploy/current)" >&2
    fi
    exit 1
  fi
  sleep 2
done

mkdir -p .deploy
if [ -f .deploy/current ] && [ "$(cat .deploy/current)" != "$TAG" ]; then
  cp .deploy/current .deploy/previous
fi
echo "$TAG" > .deploy/current
echo "release $TAG is healthy"
