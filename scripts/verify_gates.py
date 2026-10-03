"""Run the release gates against a running API and report pass/fail.

Not a substitute for the test suite — it is the thing you run after a deploy, after a
migration, or when someone asks "is tenant isolation still holding?". It talks to the
API over HTTP exactly as a client would, so it exercises the middleware, the
dependencies and the error handlers rather than the functions underneath them.

Requires the dev token issuer, so it targets a development environment.

    python scripts/verify_gates.py                       # against localhost:8000
    python scripts/verify_gates.py --base-url http://... # somewhere else
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

ANALYST_A = "ana.analyst@northwind.example.com"
ANALYST_B = "hal.analyst@harbor.example.com"
SUPERVISOR_A = "sam.supervisor@northwind.example.com"

#: Present in both organizations with different contents — the tenancy tripwire.
COLLIDING_NUMBER = "SO-1042"


class Api:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")

    def call(
        self, path: str, *, token: str | None = None, method: str = "GET", body: dict | None = None,
        organization: str | None = None,
    ) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        if organization:
            req.add_header("X-Organization", organization)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed scheme
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw or b"null")
            except json.JSONDecodeError:
                return exc.code, raw.decode(errors="replace")

    def token(self, email: str) -> str:
        status, body = self.call("/auth/dev/token", method="POST", body={"email": email})
        if status != 200:
            raise SystemExit(f"could not get a dev token for {email}: {status} {body}")
        return body["access_token"]  # type: ignore[index]

    def order_id(self, token: str, number: str) -> str:
        _, rows = self.call("/orders?limit=100", token=token)
        for row in rows:  # type: ignore[union-attr]
            if row["order_number"] == number:
                return row["id"]
        raise SystemExit(f"order {number} not found — has the dataset been seeded?")


class Report:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, actual: object, expected: object) -> None:
        ok = actual == expected
        self.passed += ok
        self.failed += not ok
        mark = "PASS" if ok else "FAIL"
        detail = "" if ok else f"   (got {actual!r}, expected {expected!r})"
        print(f"  {mark}  {name}{detail}")


def _first_order_with_events(api: Api, token: str) -> dict | None:
    """An overdue order with a carrier event trail, so the run has something to find."""
    _, rows = api.call("/orders?overdue_only=true&as_of=2026-09-12&limit=20", token=token)
    for row in rows:  # type: ignore[union-attr]
        _, detail = api.call(f"/orders/{row['id']}?as_of=2026-09-12", token=token)
        if detail.get("events"):  # type: ignore[union-attr]
            return detail  # type: ignore[return-value]
    return None


def _poll(api: Api, token: str, run_id: str, *, timeout_s: int = 45) -> dict:
    import time

    terminal = {"completed", "needs_review", "failed", "cancelled"}
    for _ in range(timeout_s):
        time.sleep(1)
        _, run = api.call(f"/runs/{run_id}", token=token)
        if run.get("status") in terminal:  # type: ignore[union-attr]
            return run  # type: ignore[return-value]
    return run  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    api = Api(args.base_url)
    report = Report()

    token_a = api.token(ANALYST_A)
    token_b = api.token(ANALYST_B)
    _, me_b = api.call("/me", token=token_b)
    org_b = me_b["organization_id"]  # type: ignore[index]

    order_a = api.order_id(token_a, COLLIDING_NUMBER)
    order_b = api.order_id(token_b, COLLIDING_NUMBER)

    print("\nAuthorization (release gate: any failure blocks release)")
    report.check("unauthenticated read is refused", api.call("/orders")[0], 401)
    report.check("tampered token is refused", api.call("/me", token=token_a[:-1] + "X")[0], 401)
    report.check(
        "another organization cannot be selected by header",
        api.call("/me", token=token_a, organization=org_b)[0],
        403,
    )

    print("\nTenant isolation")
    report.check(
        f"{COLLIDING_NUMBER} is a different row in each organization", order_a != order_b, True
    )
    report.check(
        "cross-tenant order fetch is 404, not 403",
        api.call(f"/orders/{order_b}", token=token_a)[0],
        404,
    )
    report.check(
        "own-tenant order fetch succeeds", api.call(f"/orders/{order_b}", token=token_b)[0], 200
    )

    status, detail = api.call(f"/orders/{order_a}", token=token_a)
    report.check("order detail carries an evidence id", str(detail["evidence_id"]).startswith("ord:"), True)  # type: ignore[index]
    report.check("order detail carries a record version", isinstance(detail["record_version"], int), True)  # type: ignore[index]

    print("\nRole boundaries")
    token_sup = api.token(SUPERVISOR_A)
    _, me_sup = api.call("/me", token=token_sup)
    report.check("supervisor is marked as able to approve", me_sup["can_approve"], True)  # type: ignore[index]
    _, me_a = api.call("/me", token=token_a)
    report.check("analyst is not", me_a["can_approve"], False)  # type: ignore[index]

    print("\nInvestigation")
    target = _first_order_with_events(api, token_a)
    if target is None:
        report.check("an overdue order with events exists to investigate", False, True)
    else:
        status, created = api.call(
            "/runs",
            token=token_a,
            method="POST",
            body={
                "request_text": f"Investigate order {target['order_number']}. Why is it delayed?",
                "order_number": target["order_number"],
            },
        )
        report.check("POST /runs is accepted asynchronously", status, 202)
        run = _poll(api, token_a, created["id"]) if status == 202 else {}
        report.check(
            "the run reaches a terminal state",
            run.get("status") in ("completed", "needs_review", "failed"),
            True,
        )
        result = run.get("result") or {}
        validation = result.get("validation") or {}
        report.check("every citation was supported", validation.get("unsupported"), [])
        report.check(
            "every finding carries evidence",
            bool(result.get("findings"))
            and all(f["evidence_ids"] for f in result.get("findings", [])),
            True,
        )
        report.check("tool activity was recorded", len(run.get("steps", [])) > 0, True)
        report.check(
            "the proposal is pending, not executed",
            run.get("proposal_id") is not None and run.get("status") == "needs_review",
            True,
        )
        _, cross = api.call(f"/runs/{created['id']}", token=token_b)
        report.check(
            "another organization cannot read the run",
            cross.get("error_code"),
            "resource.not_found",
        )

    print("\nOperations")
    report.check("readiness reports the database", api.call("/health/ready")[1]["checks"]["database"], "ok")  # type: ignore[index]

    print(f"\n  {report.passed} passed, {report.failed} failed\n")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
