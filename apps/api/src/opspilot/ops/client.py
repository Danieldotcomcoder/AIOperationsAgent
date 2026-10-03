"""Bounded HTTP calls. An error or malformed reply is never evidence of absence."""

import httpx
from pydantic import ValidationError

from opspilot.core.config import Settings
from opspilot.core.errors import DownstreamRejected, DownstreamUncertain
from opspilot.ops.contracts import CreateTaskRequest, TaskResponse


class OperationsClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _request(
        self, method: str, *, key: str, payload: CreateTaskRequest | None = None
    ) -> TaskResponse | None:
        try:
            with httpx.Client(timeout=self.settings.ops_api_timeout_seconds) as client:
                response = client.request(
                    method,
                    self.settings.ops_api_base_url.rstrip("/") + "/tasks",
                    headers={
                        "Authorization": "Bearer "
                        + self.settings.ops_api_service_token.get_secret_value(),
                        "Idempotency-Key": key,
                    },
                    params={"idempotency_key": key} if method == "GET" else None,
                    json=payload.model_dump(mode="json") if payload else None,
                )
            if method == "POST" and response.status_code in {400, 401, 403, 404, 409, 422}:
                raise DownstreamRejected("Operations API rejected the task")
            response.raise_for_status()
            data = response.json()
            if method == "GET" and data is None:
                return None
            result = TaskResponse.model_validate(data)
            if result.idempotency_key != key:
                raise DownstreamUncertain("Operations API returned a mismatched key")
            return result
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise DownstreamUncertain("Operations API outcome is unknown") from exc

    def lookup(self, key: str) -> TaskResponse | None:
        return self._request("GET", key=key)

    def create(self, key: str, payload: CreateTaskRequest) -> TaskResponse:
        result = self._request("POST", key=key, payload=payload)
        if result is None:
            raise DownstreamUncertain("Operations API returned no task")
        return result
