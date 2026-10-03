"""Live local API/worker smoke check. Creates a synthetic policy, then deletes it.

Run with the API and investigation worker running in fake mode:
    cd apps/api && uv run python ../../scripts/probe_documents.py
"""

import time
import uuid

import httpx


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
        token = client.post("/auth/dev/token", json={
            "email": "sam.supervisor@northwind.example.com"}).raise_for_status().json()
        client.headers["Authorization"] = f"Bearer {token['access_token']}"
        document_id = None
        try:
            text = ("# Delayed shipment escalation\nSynthetic test policy. "
                    "For delayed shipments, contact the carrier for an ETA. "
                    f"Fixture reference: {uuid.uuid4()}.")
            response = client.post("/documents", data={"title": "Synthetic retrieval probe",
                                   "effective_from": "2026-01-01"},
                                   files={"file": ("probe.md", text, "text/markdown")})
            response.raise_for_status()
            document_id = response.json()["id"]
            for _ in range(30):
                documents = client.get("/documents").raise_for_status().json()
                document = next(d for d in documents if d["id"] == document_id)
                if document["status"] in ("active", "failed"):
                    break
                time.sleep(0.5)
            assert document["status"] == "active", document
            assert document["chunk_count"] > 0
            print("PASS upload -> queue -> indexing -> active policy")
            response = client.post("/runs", json={
                "request_text": "Investigate order SO-2084 and retrieve delayed shipment policy.",
                "order_number": "SO-2084"})
            response.raise_for_status()
            run_id = response.json()["id"]
            for _ in range(30):
                run = client.get(f"/runs/{run_id}").raise_for_status().json()
                if run["status"] in ("completed", "needs_review", "failed"):
                    break
                time.sleep(0.5)
            citations = run.get("result", {}).get("policy_citations", [])
            assert citations, run
            assert run["result"]["validation"]["unsupported"] == []
            reference = citations[0]["citation_id"].removeprefix("pol:")
            version, ordinal = reference.split("#")
            path = f"/documents/versions/{version}/chunks/{ordinal}"
            passage = client.get(path).raise_for_status().json()
            assert "Synthetic" in passage["text"]
            print("PASS investigation -> granted policy citation -> readable passage")
            client.delete(f"/documents/{document_id}").raise_for_status()
            assert client.get(path).status_code == 404
            print("PASS deleted policy passage returns 404")
            document_id = None
        finally:
            if document_id:
                client.delete(f"/documents/{document_id}").raise_for_status()


if __name__ == "__main__":
    main()
