import uuid

import httpx
import pytest
import respx

from opspilot.core.clock import utcnow
from opspilot.core.config import Settings
from opspilot.core.errors import DownstreamRejected, DownstreamUncertain
from opspilot.ops.client import OperationsClient
from opspilot.ops.contracts import CreateTaskRequest

KEY = "f" * 64
CONFIG = Settings(_env_file=None, ops_api_base_url="http://127.0.0.1:8000/ops")


@respx.mock
@pytest.mark.parametrize(
    "status,data", [(500, {}), (404, {}), (200, {}), (200, {"task_id": "wrong"})]
)
def test_lookup_errors_never_mean_absence(status, data):
    respx.get("http://127.0.0.1:8000/ops/tasks").mock(
        return_value=httpx.Response(status, json=data)
    )
    with pytest.raises(DownstreamUncertain):
        OperationsClient(CONFIG).lookup(KEY)


@respx.mock
def test_only_successful_null_lookup_means_absence():
    respx.get("http://127.0.0.1:8000/ops/tasks").mock(return_value=httpx.Response(200, text="null"))
    assert OperationsClient(CONFIG).lookup(KEY) is None


@respx.mock
@pytest.mark.parametrize("status,error", [(409, DownstreamRejected), (503, DownstreamUncertain)])
def test_write_errors_classified_without_retry(status, error):
    route = respx.post("http://127.0.0.1:8000/ops/tasks").mock(
        return_value=httpx.Response(status, json={"error": "test"})
    )
    payload = CreateTaskRequest(
        organization_id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        assignee_id=uuid.uuid4(),
        due_at=utcnow(),
        description="test",
    )
    with pytest.raises(error):
        OperationsClient(CONFIG).create(KEY, payload)
    assert route.call_count == 1
