from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    settings = Settings(
        _env_file=None,
        service_name="Deskbot API",
        version="1.0.0",
        environment="development",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_root_returns_welcome_metadata(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Welcome to the Deskbot API",
        "service": "Deskbot API",
        "version": "1.0.0",
    }


def test_health_returns_status_and_utc_timestamp(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "timestamp"}
    assert body["status"] == "ok"
    timestamp = datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)


def test_status_returns_backend_metadata_only(client: TestClient) -> None:
    response = client.get("/api/v1/status")

    assert response.status_code == 200
    assert response.json() == {
        "service": "Deskbot API",
        "version": "1.0.0",
        "environment": "development",
        "status": "running",
    }
    assert "esp32" not in response.text.lower()


def test_unsupported_method_returns_method_not_allowed(client: TestClient) -> None:
    response = client.post("/api/v1/health")

    assert response.status_code == 405
    assert "GET" in response.headers["allow"]


def test_openapi_documents_response_schemas(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    document = response.json()
    paths = document["paths"]

    expected = {
        "/": "RootResponse",
        "/api/v1/health": "HealthResponse",
        "/api/v1/status": "StatusResponse",
    }
    for path, schema_name in expected.items():
        response_schema = paths[path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["$ref"] == f"#/components/schemas/{schema_name}"

    health_schema = document["components"]["schemas"]["HealthResponse"]
    assert health_schema["properties"]["status"]["const"] == "ok"
    status_schema = document["components"]["schemas"]["StatusResponse"]
    assert status_schema["properties"]["status"]["const"] == "running"


def test_environment_configuration_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKBOT_SERVICE_NAME", "Test Deskbot")
    monkeypatch.setenv("DESKBOT_VERSION", "2.4.0")
    monkeypatch.setenv("DESKBOT_ENVIRONMENT", "testing")
    settings = Settings(_env_file=None)

    with TestClient(create_app(settings)) as configured_client:
        root_response = configured_client.get("/")
        status_response = configured_client.get("/api/v1/status")

    assert root_response.json() == {
        "message": "Welcome to the Deskbot API",
        "service": "Test Deskbot",
        "version": "2.4.0",
    }
    assert status_response.json() == {
        "service": "Test Deskbot",
        "version": "2.4.0",
        "environment": "testing",
        "status": "running",
    }


def test_unknown_route_returns_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/unknown")

    assert response.status_code == 404


def test_swagger_docs_are_available(client: TestClient) -> None:
    response = client.get("/docs")

    assert response.status_code == 200
