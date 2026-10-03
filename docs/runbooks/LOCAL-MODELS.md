# Local setup and OpenRouter

The working default uses `LLM_PROVIDER=fake` and `EMBEDDING_PROVIDER=fake`. Neither
needs a key. The fake chat provider returns scripted development answers, and fake
embeddings compare hashed words, not meaning. They verify plumbing, not answer quality.

## What you need to supply

1. Create your own [OpenRouter API key](https://openrouter.ai/settings/keys). Add it
   only to the root `.env` (git-ignored), never to frontend variables or source files.
2. Set these values in that same `.env`, then restart the API and worker:

   ```dotenv
   LLM_PROVIDER=openrouter
   LLM_MODEL=openrouter/free
   OPENROUTER_API_KEY=your-key-here
   OPENROUTER_FREE_ONLY=true
   EMBEDDING_PROVIDER=fake
   ```

3. Sign in as `sam.supervisor@northwind.example.com`, open Knowledge, and upload
   `.md` or `.txt` policies with their effective dates. A synthetic starter is in
   `data/policies/delayed-shipments.md`. Use your own reviewed text for an actual pilot.
4. Hand-review ten live-model investigations and label/review twenty retrieval
   questions. The automated tests do not establish the planned quality thresholds.

The [free router](https://openrouter.ai/openrouter/free) chooses among compatible
free models, so outputs and actual model names may vary. For repeatable evaluations,
pin a currently available `vendor/model:free` that supports tool calling and JSON
responses. The explicitly supported Nemotron Ultra and Ling Flash Fin free variants
do not advertise JSON response mode, so their adapters omit `response_format` and
retain strict application-side schema/citation validation. Other models still request
JSON mode. This app refuses paid routes in free-only
mode, including the paid auto router. It sends no fallback model list or paid plugins.
OpenRouter still applies [rate and account limits](https://openrouter.ai/docs/api_reference/limits);
free service availability is not guaranteed. A provider failure yields a bounded
incomplete run. Default investigations can use up to three chat requests, including retries.

The adapter is covered by mocked HTTP tests. Live Nemotron investigations produced
validated results on 2026-09-25, but human quality review is still pending. Policy and order text are sent to whichever
provider you configure when you choose to run a real investigation.

## Embeddings are separate from chat

For free Liquid semantic embeddings through the same OpenRouter key:

```dotenv
EMBEDDING_PROVIDER=openrouter
EMBEDDING_MODEL=liquid/lfm-2.5-embedding-350m:free
EMBEDDING_DIM=1536
CHUNK_TARGET_TOKENS=500
```

Liquid returns 1024 native components; the adapter appends zeros to fit the existing
1536-component storage without changing cosine similarity. Retrieval uses the distinct
`:pad1536-v1` identity. Existing fake/OpenAI versions must be indexed as a new revision
before Liquid search can retrieve them. Inputs over 500 UTF-8 bytes are rejected;
the existing chunker already bounds passages by bytes. Keep retrieval queries concise.
The first live probe hit the account's daily free-request limit; successful Liquid
indexing remains unverified. No separate OpenAI key is needed for this route.

OpenRouter's [model card](https://openrouter.ai/liquid/lfm-2.5-embedding-350m:free)
documents the context/dimensions and that successful requests may be retained for
model training. The current validation corpus contains synthetic data only.

OpenRouter chat does not require an OpenAI key. Leave `EMBEDDING_PROVIDER=fake` to
try the complete pipeline at no API cost, with limited word-overlap retrieval.

For semantic embeddings, the implemented paid option is:

```dotenv
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
OPENAI_API_KEY=your-separate-openai-key
```

Both indexing and query embeddings reserve estimated spend through Redis before the
provider call. On an uncertain provider failure, the reservation is retained. Embedding
spend is included in global budget counters, but not in the case's chat-cost display.
Vectors from different models are never mixed. After changing embedding provider,
upload a new revision with changed text and a later effective date; automated bulk
re-indexing of existing versions is not implemented. A free local semantic embedding
model is also not implemented yet.

## Policy behavior

Uploads are limited to UTF-8 text, 2 MB and 256 passages. Titles are immutable for a
document; select Add revision to update its contents. Content is deduplicated within
the organization. A same-content upload cannot silently change the effective date.
Revisions must use increasing dates, and one pending revision is allowed at a time.
Scheduled revisions do not hide today's version: the previous effective interval ends
on the new revision's start date. Old citations remain readable until the document is
deleted. Deletion removes chunks, vectors and raw text; metadata and run records remain.

Chunking preserves Markdown headings and uses conservative UTF-8-byte token counts
and overlap so it works offline without downloading tokenizer data. It produces
smaller passages than a 500-model-token tokenizer would. No semantic recall claim is
made until the labelled retrieval evaluation is run.

If indexing fails or stays pending after a broker interruption, restart the worker and
use Retry indexing. Duplicate deliveries are harmless. Retry is manual in this milestone;
an automatic indexing recovery sweep is not implemented.

## Start and stop

From the repository root:

```powershell
docker compose up -d postgres redis
```

In separate terminals, from `apps/api`:

```powershell
uv run uvicorn opspilot.main:app --host 127.0.0.1 --port 8000
uv run celery -A opspilot.workers.app:celery_app worker -Q investigate --pool=solo --concurrency=1
```

From the root, `pnpm dev` starts the frontend. When done, stop those three foreground
processes with Ctrl+C, then run `docker compose stop postgres redis`. This retains data
volumes. Do not use `down -v` unless you intend to erase your local data.

## Reproducible verification

With the migrated PostgreSQL service running, from `apps/api`:

```powershell
uv run pytest --integration -q
uv run ruff check src tests
```

With the API and worker also running in fake mode:

```powershell
uv run python ../../scripts/probe_documents.py
uv run python ../../scripts/verify_gates.py
uv run python ../../scripts/probe_agent.py
```

The document probe creates a synthetic policy and investigation and deletes the policy
afterward. Its metadata and run remain as audit evidence. Full repository mypy and the
strict tenancy audit have pre-existing failures recorded in HANDOFF.md.
