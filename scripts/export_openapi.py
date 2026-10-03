"""Write the API's OpenAPI schema for the shared TypeScript contracts.

The Pydantic models are the source of truth; ``packages/contracts`` generates its
types from this file, so the SPA cannot drift from what the API actually returns.
No server, database or credentials are needed: the schema comes from the app object.

    uv run python ../../scripts/export_openapi.py      # from apps/api
    pnpm contracts:generate                            # from the root: export + generate
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

OUTPUT = REPO_ROOT / "packages" / "contracts" / "openapi.json"


def main() -> int:
    # The schema must not depend on whoever runs this: fake providers need no keys,
    # and development mode includes the dev token route the SPA signs in with.
    os.environ.update({"APP_ENV": "development", "LLM_PROVIDER": "fake"})
    os.environ.update({"EMBEDDING_PROVIDER": "fake", "AUTH_MODE": "dev"})
    from opspilot.main import create_app

    schema = create_app().openapi()
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} ({len(schema['paths'])} paths)")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
