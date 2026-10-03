# Deploy, roll back, back up and restore (Week 8)

Status: **prepared, not yet exercised on a server.** The production compose file,
scripts and this runbook exist and were validated locally as far as a laptop allows
(see "What has been verified"). Nothing has been deployed. Week 8 also needs things
only the owner can provide — listed first.

## What the owner must provide

1. A VPS (2 vCPU / 4 GB, Ubuntu LTS) with Docker Engine + Compose plugin, a DNS name
   pointing at it, and ports 80/443 open. Nothing else is published.
2. An Auth0 tenant (open question 4 in HANDOFF): an API (audience) and a single-page
   application. The API already verifies Auth0 tokens (`AUTH_MODE=auth0`, ADR 0008).
   **The SPA's Auth0 sign-in is not built yet** — it signs in through the local dev
   issuer only, which production refuses. Add it once the tenant exists, so it can be
   tested against the real thing.
3. A container registry (e.g. GHCR) and an off-server backup target reachable by
   `rclone`, plus an `age` key pair; keep the private key off the VPS.
4. A model key (OpenRouter free-only works) and reviewed policy documents.

## Build (off the server)

```sh
sh infra/scripts/build-images.sh 2026-10-03.1 ghcr.io/your-account
```

Two images: `opspilot-api` (API, workers, beat and migrations — one image, several
entrypoints, ADR 0006) and `opspilot-web` (Caddy serving the static SPA and proxying
`/api`). Both install strictly from the lockfiles; a build never re-resolves.

## First deploy

```sh
git clone … && cd opspilot
cp infra/compose/.env.production.example .env.production   # fill in; chmod 600
sh infra/scripts/deploy.sh 2026-10-03.1
# Identities: memberships are rows, not tokens. Create the pilot organization and
# members with the Auth0 subjects (scripts/seed_dev_users.py shows the shape; it
# refuses to run outside development).
```

`deploy.sh` pulls the tag, runs `alembic upgrade head` as a one-shot, starts the
stack, waits for `/health/ready`, and records `.deploy/current` and `.deploy/previous`.

## Roll back

```sh
sh infra/scripts/deploy.sh "$(cat .deploy/previous)"
```

Migrations are expand → migrate → contract across releases, so the previous image
runs against the migrated schema. Never ship a destructive migration in the same
release as the code that stops using a column.

## Back up and restore

```sh
sh infra/scripts/backup.sh                                  # cron: daily, 02:00 UTC
sh infra/scripts/restore.sh backups/opspilot-<stamp>.dump.age /secure/age-key.txt
```

The dump streams through `age` straight to an encrypted file, then `rclone` copies
it off the server and prunes copies older than `BACKUP_KEEP_DAYS`. Restore never
touches the live database: it builds `opspilot_restore_<stamp>`, restores into it and
prints row counts and the migration head beside live, for a person to judge.

Pilot targets: at most 24 hours of lost data; recovery within four hours. Verify both
in a drill on the server and record the timings here.

## Operating limits already in place

- **Concurrency**: investigate worker `-c 2`, execute worker `-c 1` (serialized
  writes), one beat. Memory caps per container in the compose file.
- **Request limits**: per-user daily run quota and daily/monthly spend caps are
  enforced atomically in Redis before any model call (fail closed if Redis is down).
- **Log rotation**: every container uses json-file with 10 MB × 5; Caddy rolls its
  access log at 10 MB × 5.
- **Disable writes**: set `FEATURE_WRITES_ENABLED=false` (and/or
  `FEATURE_AGENT_ENABLED=false`) in `.env.production`, then
  `docker compose … up -d` to restart with it. Settings load at start; this is not
  a live toggle.
- **Health**: `/health/live` (process), `/health/ready` (database + Redis). Point an
  external uptime monitor at `https://<domain>/health/ready` and alert on two
  consecutive failures; alert on `reconciliation_required > 0` from the Metrics page.

## What has been verified (2026-10-03, locally)

- Both images build from the lockfiles: `opspilot-api` (765 MB, 71 packages, no
  system packages) and `opspilot-web` (94 MB, SPA built inside). `.dockerignore`
  keeps `.env`, keys, `.data` and host `.venv`/`node_modules` out of the context.
- The production compose stack ran on Docker Desktop under a separate project name
  with placeholder secrets (`APP_ENV=production`, `AUTH_MODE=auth0`): the one-shot
  migration built the schema; API, both workers (Celery ping health checks), beat,
  PostgreSQL, Redis and Caddy came up healthy.
- Through Caddy over HTTPS (internal CA for `localhost`): `/health/ready` reports
  `env: production`; the SPA and its client routes are served; `/api/me` reaches the
  API (401 without a token); the dev token route and `/docs` are absent (404);
  HTTP redirects to HTTPS; CSP, HSTS, nosniff and referrer headers are set and the
  `Server` header is removed.
- Only Caddy publishes ports. The data network is internal: PostgreSQL cannot reach
  the internet, while the API reaches the model provider.
- A custom-format `pg_dump` restored into an isolated database with identical
  migration head and row counts. `age` and `rclone` were not exercised (not installed
  on the dev machine).
- Not verified: real TLS issuance, Auth0 sign-in (the SPA side is not built), the
  scripts on a Linux host, `deploy.sh` with registry pulls, a timed restore, rollback
  across a real migration, and behaviour under real traffic.
