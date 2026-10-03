-- Runs once, on first initialization of the data volume.
-- Extensions are created here rather than in a migration because they need
-- superuser and should not be part of the application's reversible history.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Deterministic, sortable UUIDs are generated in the application (core/ids.py);
-- pgcrypto is here only for gen_random_uuid() as a defensive default.
