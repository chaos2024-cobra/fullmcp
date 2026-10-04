"""Standalone Gnani gateway with a small, deterministic input contract."""
from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def tts_payload(body: dict[str, Any]) -> dict[str, Any]:
    text = str(body.get("text", "")).strip()
    if not text:
        raise ValueError("text is required")
    return {
        "text": text,
        "voice": body.get("voice", "Nalini"),
        "model": body.get("model", "timbre-v2.5"),
        "language": body.get("language", "en-IN"),
        "speed": body.get("speed", 1.0),
        "audio_config": body.get("audio_config", {
            "sample_rate": 48000, "num_channels": 1, "sample_width": 2,
            "encoding": "linear_pcm", "container": "wav",
        }),
    }


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "raahi-gnani-agent"})


async def tts(request: Request) -> JSONResponse:
    rid = str(uuid.uuid4())
    try:
        payload = tts_payload(await request.json())
        key = os.getenv("GNANI_API_KEY")
        base = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai").rstrip("/")
        if not key:
            return JSONResponse({"ok": False, "request_id": rid,
                                 "error": "GNANI_API_KEY is not configured"}, status_code=503)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{base}/api/v1/tts/inference",
                json=payload,
                headers={"X-API-Key-ID": key, "Content-Type": "application/json"},
            )
        if response.status_code >= 400:
            detail = response.text[:300].replace("\n", " ").strip()
            return JSONResponse({"ok": False, "request_id": rid,
                                 "error": f"Gnani HTTP {response.status_code}: {detail}"},
                                status_code=502)
        return JSONResponse({"ok": True, "request_id": rid,
                             "execution_mode": "real", "data": response.json()})
    except ValueError as exc:
        return JSONResponse({"ok": False, "request_id": rid, "error": str(exc)}, status_code=400)
    except (httpx.HTTPError, ValueError) as exc:
        return JSONResponse({"ok": False, "request_id": rid,
                             "error": f"Gnani request failed: {type(exc).__name__}"},
                            status_code=502)


app = Starlette(routes=[
    Route("/health", health, methods=["GET"]),
    Route("/tts", tts, methods=["POST"]),
])
