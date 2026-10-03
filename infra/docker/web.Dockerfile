# syntax=docker/dockerfile:1.7
# The SPA is a static build artifact; Caddy serves it in production.
#
#   docker build -f infra/docker/web.Dockerfile --target runtime -t opspilot-web:<tag> .
#
# Installs strictly from pnpm-lock.yaml and copies only the workspace packages the
# build needs; .dockerignore keeps secrets and host node_modules out of the context.

FROM node:22-alpine AS base
ENV PNPM_HOME=/pnpm PATH="/pnpm:$PATH"
RUN corepack enable
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/contracts/package.json packages/contracts/
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

FROM deps AS dev
COPY apps/web apps/web
COPY packages/contracts packages/contracts
EXPOSE 5173
CMD ["pnpm", "--filter", "@opspilot/web", "dev", "--host", "0.0.0.0"]

FROM deps AS build
COPY apps/web apps/web
COPY packages/contracts packages/contracts
# Same origin in production: Caddy serves the SPA and proxies /api to the API.
ARG VITE_API_BASE_URL=/api
RUN pnpm --filter @opspilot/web build

# Static output only. No node process runs in production.
FROM caddy:2-alpine AS runtime
COPY --from=build /app/apps/web/dist /srv
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
