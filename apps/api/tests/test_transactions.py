"""A request is reported successful only after its unit of work has committed."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from opspilot.auth.dependencies import DbSession
from opspilot.db import session as session_module


class _CommitFails:
    """Stands in for a session whose COMMIT is lost (deadlock, dropped connection)."""

    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        raise RuntimeError("commit lost")

    def in_transaction(self) -> bool:
        return True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_failed_commit_is_never_reported_as_accepted(monkeypatch) -> None:
    session = _CommitFails()
    monkeypatch.setattr(session_module, "get_session_factory", lambda: lambda: session)
    app = FastAPI()

    @app.post("/write", status_code=202)
    def write(_: DbSession) -> dict[str, str]:
        return {"status": "accepted"}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/write")

    # With a request-scoped session the 202 was already on the wire when COMMIT failed.
    assert response.status_code == 500
    assert session.rolled_back and session.closed
