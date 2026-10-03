#!/usr/bin/env sh
# Build and push both production images for one release tag. Run off the server
# (CI or a workstation), never on the 4 GB VPS.
#
#   sh infra/scripts/build-images.sh 2026-10-03.1 ghcr.io/your-account
set -eu

cd "$(dirname "$0")/../.."
TAG=${1:?usage: build-images.sh <tag> <registry-prefix>}
REGISTRY=${2:?usage: build-images.sh <tag> <registry-prefix>}

docker build -f infra/docker/api.Dockerfile --target runtime -t "$REGISTRY/opspilot-api:$TAG" .
docker build -f infra/docker/web.Dockerfile --target runtime -t "$REGISTRY/opspilot-web:$TAG" .
docker push "$REGISTRY/opspilot-api:$TAG"
docker push "$REGISTRY/opspilot-web:$TAG"
echo "pushed $REGISTRY/opspilot-api:$TAG and $REGISTRY/opspilot-web:$TAG"
