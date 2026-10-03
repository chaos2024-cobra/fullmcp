import json

import pytest
from httpx import ASGITransport, AsyncClient

from capabilities import TOOL_NAMES
from server import app


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
