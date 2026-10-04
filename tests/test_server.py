import json

import pytest
from httpx import ASGITransport, AsyncClient

from capabilities import TOOL_NAMES
from server import app
from gnani_agent import AGENT_CARD, a2a_text, tts_payload


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "raahi-unified-mcp"


def test_exact_tool_surface():
    assert len(TOOL_NAMES) == 21
    assert len(set(TOOL_NAMES)) == 21


@pytest.mark.asyncio
async def test_payment_safety():
    from server import execute
    result = await execute("collect_payment", {
        "order_amount": 101, "confirmed_amount": 100,
        "currency": "INR", "purpose": "courier",
    })
    assert result["ok"] is False
    assert result["error"]["code"] == "AMOUNT_EXCEEDS_CONFIRMATION"


@pytest.mark.asyncio
async def test_deterministic_mock_failure_scenarios(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    from server import execute
    result = await execute("track_shipment", {"awb": "x", "scenario": "NDR"})
    assert result["data"]["state"] == "NDR"
    assert result["data"]["delivery_scan"] is False


def test_provider_authentication_and_paths(monkeypatch):
    from server import provider_request

    monkeypatch.setenv("GNANI_BASE_URL", "https://api.vachana.ai")
    monkeypatch.setenv("GNANI_API_KEY", "gnani-test")
    method, url, headers, payload, _ = provider_request("gnani", "speak_reply", {"text": "hello"})
    assert method == "POST"
    assert url.endswith("/api/v1/tts/inference")
    assert headers["X-API-Key-ID"] == "gnani-test"
    assert "Authorization" not in headers
    assert payload["voice"] == "Nalini"
    assert payload["model"] == "timbre-v2.5"
    assert payload["audio_config"]["container"] == "wav"

    monkeypatch.setenv("DELHIVERY_BASE_URL", "https://track.delhivery.com")
    monkeypatch.setenv("DELHIVERY_API_KEY", "delhivery-test")
    method, url, headers, _, params = provider_request(
        "delhivery", "track_shipment", {"awb": "123"})
    assert method == "GET"
    assert url.endswith("/api/v1/packages-json/")
    assert headers["Authorization"] == "Token delhivery-test"
    assert params == {"waybill": "123"}


def test_unsupported_provider_capability_is_explicit(monkeypatch):
    from server import execute

    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("GNANI_BASE_URL", "https://api.vachana.ai")
    monkeypatch.setenv("GNANI_API_KEY", "gnani-test")
    result = __import__("asyncio").run(execute("navigate_ivr", {"dtmf_sequence": "1"}))
    assert result["ok"] is False
    assert result["error"]["code"] == "PROVIDER_CAPABILITY_UNIMPLEMENTED"


@pytest.mark.asyncio
async def test_gnani_required_inputs():
    from server import execute

    result = await execute("speak_reply", {})
    assert result["error"]["code"] == "TEXT_REQUIRED"
    result = await execute("transcribe_speech", {})
    assert result["error"]["code"] == "AUDIO_REQUIRED"


def test_standalone_gnani_gateway_builds_complete_payload():
    payload = tts_payload({"text": "Hello from Raahi."})
    assert payload["text"] == "Hello from Raahi."
    assert payload["voice"] == "Nalini"
    assert payload["model"] == "timbre-v2.5"
    assert payload["audio_config"]["container"] == "wav"

def test_gnani_gateway_exposes_a2a_card_and_message_contract():
    assert AGENT_CARD["protocolVersion"] == "0.3.0"
    assert AGENT_CARD["preferredTransport"] == "JSONRPC"
    assert AGENT_CARD["url"].endswith("/a2a")
    assert a2a_text({"message": {"parts": [{"kind": "text", "text": "Hello"}]}}) == "Hello"
