"""Standalone Gnani gateway with a small, deterministic input contract."""
from __future__ import annotations

import base64
import contextlib
import json
import os
import uuid
from typing import Any

import httpx
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.routing import BaseRoute, Match


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

async def synthesize(body: dict[str, Any], rid: str) -> dict[str, Any]:
    payload = tts_payload(body)
    key = os.getenv("GNANI_API_KEY")
    base = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai").rstrip("/")
    if not key:
        raise RuntimeError("GNANI_API_KEY is not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{base}/api/v1/tts/inference",
            json=payload,
            headers={"X-API-Key-ID": key, "Content-Type": "application/json"},
        )
    if response.status_code >= 400:
        detail = response.text[:300].replace("\n", " ").strip()
        raise RuntimeError(f"Gnani HTTP {response.status_code}: {detail}")
    content_type = response.headers.get("content-type", "application/octet-stream")
    try:
        data: Any = response.json()
    except ValueError:
        data = {
            "audio_base64": base64.b64encode(response.content).decode("ascii"),
            "content_type": content_type,
            "bytes": len(response.content),
        }
    return {"ok": True, "request_id": rid, "execution_mode": "real", "data": data}

async def tts(request: Request) -> JSONResponse:
    rid = str(uuid.uuid4())
    try:
        return JSONResponse(await synthesize(await request.json(), rid))
    except ValueError as exc:
        return JSONResponse({"ok": False, "request_id": rid, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "request_id": rid, "error": str(exc)},
                            status_code=503 if "not configured" in str(exc) else 502)
    except (httpx.HTTPError, ValueError) as exc:
        return JSONResponse({"ok": False, "request_id": rid,
                             "error": f"Gnani request failed: {type(exc).__name__}"},
                            status_code=502)

AGENT_CARD = {
    "protocolVersion": "0.3.0",
    "name": "Raahi Gnani Voice Agent",
    "description": "Converts non-empty text to speech through the external Gnani provider.",
    "url": "https://raahi-gnani-agent.onrender.com/a2a",
    "preferredTransport": "JSONRPC",
    "version": "1.0.0",
    "capabilities": {"streaming": False, "pushNotifications": False},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text", "data"],
    "skills": [{
        "id": "gnani-tts",
        "name": "Gnani text to speech",
        "description": "Generate WAV audio from supplied text.",
        "tags": ["voice", "text-to-speech", "gnani"],
        "examples": ["Say hello from Raahi"],
        "inputModes": ["text"],
        "outputModes": ["data"],
    }],
}

async def agent_card(request: Request) -> JSONResponse:
    return JSONResponse(AGENT_CARD)

def a2a_text(params: dict[str, Any]) -> str:
    message = params.get("message") or {}
    parts = message.get("parts") or []
    for part in parts:
        if part.get("kind") == "text" and str(part.get("text", "")).strip():
            return str(part["text"]).strip()
    raise ValueError("A2A message must contain non-empty text")

async def a2a(request: Request) -> JSONResponse:
    body = await request.json()
    request_id = body.get("id")
    if body.get("jsonrpc") != "2.0" or body.get("method") != "message/send":
        return JSONResponse({"jsonrpc": "2.0", "id": request_id,
                             "error": {"code": -32601, "message": "Only message/send is supported"}},
                            status_code=400)
    try:
        text = a2a_text(body.get("params") or {})
        result = await synthesize({"text": text}, str(uuid.uuid4()))
        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "contextId": task_id,
            "status": {"state": "completed"},
            "artifacts": [{"artifactId": str(uuid.uuid4()), "parts": [
                {"kind": "data", "data": result},
            ]}],
        }
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": task})
    except ValueError as exc:
        return JSONResponse({"jsonrpc": "2.0", "id": request_id,
                             "error": {"code": -32602, "message": str(exc)}}, status_code=400)
    except (RuntimeError, httpx.HTTPError) as exc:
        return JSONResponse({"jsonrpc": "2.0", "id": request_id,
                             "error": {"code": -32000, "message": str(exc)}}, status_code=502)

async def mcp_speak(arguments: dict[str, Any]) -> CallToolResult:
    class _Request:
        async def json(self):
            return arguments
    result = await tts(_Request())
    body = json.loads(result.body)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(body))],
        isError=not body.get("ok", False),
    )

async def list_tools() -> ListToolsResult:
    return ListToolsResult(tools=[Tool(
        name="speak_reply",
        description="Raahi capability: speak_reply.",
        inputSchema={"type": "object", "required": ["text"],
                     "properties": {"text": {"type": "string"}}},
        annotations=ToolAnnotations(readOnlyHint=False),
    )])

async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    if name != "speak_reply":
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(
                {"ok": False, "error": "Unknown tool"}))], isError=True)
    return await mcp_speak(arguments)

mcp_server = Server(
    "raahi-gnani-agent", version="1.0.0",
    on_list_tools=lambda _ctx, _params: list_tools(),
    on_call_tool=lambda _ctx, params: call_tool(params.name, params.arguments or {}),
)
session_manager = StreamableHTTPSessionManager(
    mcp_server, json_response=True,
    security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

async def mcp_endpoint(scope, receive, send):
    await session_manager.handle_request(scope, receive, send)

class MCPRoute(BaseRoute):
    def matches(self, scope):
        if scope["type"] == "http" and scope["path"] == "/mcp" and scope["method"] in {"GET", "POST", "DELETE"}:
            return Match.FULL, {}
        return Match.NONE, {}

    async def handle(self, scope, receive, send):
        await mcp_endpoint(scope, receive, send)

@contextlib.asynccontextmanager
async def lifespan(app):
    async with session_manager.run():
        yield

app = Starlette(routes=[
    Route("/health", health, methods=["GET"]),
    Route("/tts", tts, methods=["POST"]),
    Route("/.well-known/agent-card.json", agent_card, methods=["GET"]),
    Route("/a2a", a2a, methods=["POST"]),
    MCPRoute(),
], lifespan=lifespan)
