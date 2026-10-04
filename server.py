"""Raahi's unified MCP backend.

This uses the MCP Python SDK 2 low-level server and Streamable HTTP session
manager. Provider adapters never turn a configured provider failure into a
mock success.
"""
from __future__ import annotations

import contextlib, hashlib, json, logging, os, re, time, uuid
from typing import Any

import httpx
import capabilities
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (CallToolResult, ListToolsResult, TextContent, Tool,
                        ToolAnnotations)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from capabilities import TOOL_NAMES, registry

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("raahi")
TOOLS = registry()
CASES: dict[str, dict[str, Any]] = {}

SCHEMAS = {
    name: {"type": "object", "additionalProperties": True}
    for name in TOOL_NAMES
}
SCHEMAS.update({
    "speak_reply": {"type": "object", "required": ["text"],
                   "properties": {"text": {"type": "string"},
                                  "voice": {"type": "string"},
                                  "model": {"type": "string"},
                                  "language": {"type": "string"},
                                  "speed": {"type": "number"},
                                  "audio_config": {"type": "object"}}},
    "transcribe_speech": {"type": "object", "required": ["audio"],
                          "properties": {"audio": {"type": "string"},
                                         "language_code": {"type": "string"}}},
    "collect_payment": {"type": "object", "required": ["order_amount", "purpose"],
                        "properties": {"order_amount": {"type": "number"},
                                       "purpose": {"type": "string"},
                                       "currency": {"type": "string"}}},
    "request_aa_consent": {"type": "object", "required": ["purpose"]},
    "call_bank_rm_or_desk": {"type": "object", "required": ["phone", "checklist_item_id"]},
})

def request_id() -> str:
    return str(uuid.uuid4())

def envelope(tool: str, provider: str, mode: str, data: Any = None,
             error: dict[str, Any] | None = None, rid: str | None = None) -> dict:
    return {"ok": error is None, "provider": provider, "execution_mode": mode,
            "tool": tool, "request_id": rid or request_id(), "data": data,
            "error": error}

def err(code: str, message: str, retryable: bool = False) -> dict:
    return {"code": code, "message": message, "retryable": retryable}

def scenario(args: dict) -> str:
    return str(args.get("scenario") or os.getenv("MOCK_SCENARIO", "SUCCESS")).upper()

def mock_result(tool: str, args: dict, provider="raahi_mock") -> dict:
    sc = scenario(args)
    if sc == "TIMEOUT":
        return envelope(tool, provider, "mock", error=err("MOCK_TIMEOUT", "Deterministic timeout", True))
    if sc in {"DECLINED", "PAYMENT_DECLINED"} and tool == "collect_payment":
        return envelope(tool, provider, "mock", {"status": "DECLINED"})
    if sc == "CONSENT_REJECTED" and tool == "request_aa_consent":
        return envelope(tool, provider, "mock", {"consent_id": None, "status": "REJECTED"})
    if sc == "NDR" and tool == "track_shipment":
        return envelope(tool, provider, "mock", {"state": "NDR", "delivery_scan": False, "events": []})
    if sc == "UNSERVICEABLE" and tool == "check_serviceability":
        return envelope(tool, provider, "mock", {"serviceable": False, "modes": []})
    if sc == "NO_SLOT" and tool == "book_appointment":
        return envelope(tool, provider, "mock", {"status": "NO_SLOT"})
    if sc == "OTP_FAILURE" and tool == "esign_document":
        return envelope(tool, provider, "mock", {"status": "OTP_FAILURE", "signed": False})
    digest = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:12]
    data: dict[str, Any] = {"status": "SUCCESS", "reference": f"mock-{digest}"}
    if tool == "collect_payment": data.update(status="MOCK_SUCCESS", transaction_id=f"txn-{digest}")
    if tool == "check_serviceability": data.update(serviceable=True, modes=["EXPRESS"])
    if tool == "track_shipment": data.update(state="IN_TRANSIT", delivery_scan=False, events=[])
    if tool == "book_appointment": data.update(appointment_id=f"apt-{digest}", status="BOOKED")
    if tool == "create_web_checklist": data.update(checklist_id=f"chk-{digest}",
                                                    url=f"https://example.invalid/checklist/{digest}",
                                                    items=args.get("checklist_items", []))
    if tool == "issue_travel_insurance": data.update(policy_id=f"policy-{digest}",
                                                     execution_mode="mock",
                                                     certificate_reference=f"mock-cert-{digest}")
    return envelope(tool, provider, "mock", data)

def configured(provider: str) -> bool:
    if provider == "pinelabs":
        return bool(os.getenv("PINELABS_BASE_URL") and (
            os.getenv("PINELABS_ACCESS_TOKEN") or
            (os.getenv("PINELABS_CLIENT_ID") and os.getenv("PINELABS_CLIENT_SECRET"))))
    return bool(os.getenv(f"{provider.upper()}_BASE_URL") and
                os.getenv(f"{provider.upper()}_API_KEY"))

def provider_request(provider: str, tool: str, args: dict) -> tuple[str, str, dict, dict, dict]:
    """Map Raahi tool names to provider-specific paths and authentication."""
    base = os.environ[f"{provider.upper()}_BASE_URL"].rstrip("/")
    if provider == "gnani":
        paths = {"transcribe_speech": "/stt/v3", "speak_reply": "/api/v1/tts/inference"}
        if tool not in paths:
            raise ValueError(f"{tool} is not implemented by the Gnani adapter")
        if tool == "transcribe_speech":
            return "MULTIPART", f"{base}{paths[tool]}", {
                "X-API-Key-ID": os.environ["GNANI_API_KEY"],
            }, args, {}
        payload = {
            "text": args.get("text", ""),
            "voice": args.get("voice", "Nalini"),
            "model": args.get("model", "timbre-v2.5"),
            "language": args.get("language", "en-IN"),
            "speed": args.get("speed", 1.0),
            "audio_config": args.get("audio_config", {
                "sample_rate": 48000, "num_channels": 1, "sample_width": 2,
                "encoding": "linear_pcm", "container": "wav",
            }),
        }
        return "POST", f"{base}{paths[tool]}", {
            "X-API-Key-ID": os.environ["GNANI_API_KEY"],
            "Content-Type": "application/json",
        }, payload, {}
    if provider == "delhivery":
        token = os.environ["DELHIVERY_API_KEY"]
        headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
        if tool == "track_shipment":
            return "GET", f"{base}/api/v1/packages-json/", headers, {}, {
                "waybill": args.get("awb", "")}
        if tool == "check_serviceability":
            return "GET", f"{base}/api/cms/pin-codes/json/", headers, {}, {
                "token": token, "filter_codes": args.get("pincode", "")}
        if tool == "schedule_document_pickup":
            return "POST", f"{base}/api/cmu/create.json", headers, args, {}
        raise ValueError(f"{tool} is not implemented by the Delhivery adapter")
    if provider == "pinelabs":
        if tool != "collect_payment":
            raise ValueError(f"{tool} requires a provider-specific Pine Labs contract")
        return "POST", f"{base}/api/pay/v1/orders", {
            "Authorization": f"Bearer {os.getenv('PINELABS_ACCESS_TOKEN', '')}",
            "Content-Type": "application/json", "Accept": "application/json",
        }, args, {}
    raise ValueError(f"Unsupported provider: {provider}")

async def provider_call(tool: str, args: dict, provider: str) -> dict:
    if not configured(provider):
        return envelope(tool, provider, "real", error=err("PROVIDER_NOT_CONFIGURED",
                         f"{provider} base URL and credentials are required"))
    try:
        method, url, headers, payload, params = provider_request(provider, tool, args)
        async with httpx.AsyncClient(timeout=20) as client:
            if method == "MULTIPART":
                audio = payload.get("audio") or payload.get("audio_file")
                if not audio:
                    raise ValueError("transcribe_speech requires audio or audio_file")
                response = await client.post(
                    url, headers=headers,
                    files={"audio_file": ("audio.wav", audio, "audio/wav")},
                    data={"language_code": payload.get("language_code", "en-IN")},
                    params=params,
                )
            else:
                response = await client.request(
                    method, url, json=payload, headers=headers, params=params)
            response.raise_for_status()
            body = response.json()
        return envelope(tool, provider, "real", body)
    except ValueError as exc:
        return envelope(tool, provider, "real",
                        error=err("PROVIDER_CAPABILITY_UNIMPLEMENTED", str(exc)))
    except httpx.TimeoutException:
        return envelope(tool, provider, "real", error=err(f"{provider.upper()}_TIMEOUT", "Provider timed out", True))
    except httpx.HTTPStatusError as exc:
        logger.warning("provider request failed tool=%s provider=%s status=%s",
                       tool, provider, exc.response.status_code)
        detail = exc.response.text[:300].replace("\n", " ").strip()
        return envelope(tool, provider, "real", error=err(
            f"{provider.upper()}_HTTP_{exc.response.status_code}",
            f"{provider} rejected the request with HTTP {exc.response.status_code}"
            + (f": {detail}" if detail else ""),
            exc.response.status_code >= 500))
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("provider request failed tool=%s provider=%s error=%s",
                       tool, provider, type(exc).__name__)
        return envelope(tool, provider, "real",
                        error=err(f"{provider.upper()}_ERROR", "Provider request failed", True))

def validate(tool: str, args: dict) -> dict | None:
    if tool == "speak_reply" and not str(args.get("text", "")).strip():
        return err("TEXT_REQUIRED", "Speech text is required")
    if tool == "transcribe_speech" and not (args.get("audio") or args.get("audio_file")):
        return err("AUDIO_REQUIRED", "Audio is required for transcription")
    if tool == "collect_payment":
        amount = args.get("order_amount")
        confirmed = args.get("confirmed_amount")
        if not isinstance(amount, (int, float)) or amount <= 0:
            return err("INVALID_AMOUNT", "Payment amount must be positive")
        if not args.get("currency") or not args.get("purpose"):
            return err("INCOMPLETE_PAYMENT", "Currency and purpose are required")
        if confirmed is None:
            return err("CONFIRMED_AMOUNT_REQUIRED", "Confirmed amount is required")
        if amount > confirmed:
            return err("AMOUNT_EXCEEDS_CONFIRMATION", "Requested amount exceeds confirmed amount")
    if tool == "call_bank_rm_or_desk" and not re.fullmatch(r"\+?[1-9]\d{7,14}", str(args.get("phone", ""))):
        return err("INVALID_PHONE", "Only a confirmed international phone number is accepted")
    if tool == "navigate_ivr" and not args.get("dtmf_sequence"):
        return err("IVR_MAPPING_UNKNOWN", "An explicit mapped DTMF sequence is required")
    return None

async def execute(tool: str, args: dict) -> dict:
    rid = request_id()
    bad = validate(tool, args)
    if bad:
        return envelope(tool, TOOLS[tool]["provider"], "mock" if tool in capabilities.MOCK_ONLY_TOOLS else TOOLS[tool]["execution_mode"], error=bad, rid=rid)
    if tool == "pull_case_status_into_call":
        case = CASES.get(args.get("case_id") or args.get("applicant_id"))
        return envelope(tool, "internal_workflow", "mock", case or {"outstanding_items": [], "status": "NOT_FOUND"}, rid=rid)
    meta = TOOLS[tool]
    if tool in capabilities.MOCK_ONLY_TOOLS:
        return mock_result(tool, args)
    if meta["execution_mode"] == "conditional" and not configured("pinelabs"):
        return mock_result(tool, args)
    if meta["execution_mode"] == "conditional" and os.getenv("MOCK_MODE", "false").lower() == "true":
        return mock_result(tool, args, "pinelabs")
    if meta["execution_mode"] == "real" and os.getenv("MOCK_MODE", "false").lower() == "true" and not configured(meta["provider"]):
        return mock_result(tool, args)
    return await provider_call(tool, args, meta["provider"])

def tool_def(name: str) -> Tool:
    return Tool(name=name, description=f"Raahi capability: {name}.", inputSchema=SCHEMAS[name],
                annotations=ToolAnnotations(readOnlyHint=name.startswith(("read_", "track_", "estimate_"))))

async def list_tools() -> ListToolsResult:
    return ListToolsResult(tools=[tool_def(name) for name in TOOL_NAMES])

async def call_tool(name: str, arguments: dict) -> CallToolResult:
    if name not in TOOLS:
        result = envelope(name, "internal_workflow", "mock", error=err("UNKNOWN_TOOL", "Unknown tool"))
    else:
        started = time.monotonic()
        result = await execute(name, arguments or {})
        logger.info("request_id=%s tool=%s provider=%s execution_mode=%s latency_ms=%d success=%s",
                    result["request_id"], name, result["provider"], result["execution_mode"],
                    int((time.monotonic() - started) * 1000), result["ok"])
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))],
                          isError=not result["ok"])

mcp_server = Server("raahi-unified-mcp", version="1.0.0",
                    on_list_tools=lambda _ctx, _params: list_tools(),
                    on_call_tool=lambda _ctx, params: call_tool(
                        params.name, params.arguments or {}))
security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
session_manager = StreamableHTTPSessionManager(mcp_server, json_response=True,
                                                security_settings=security)

@contextlib.asynccontextmanager
async def lifespan(app):
    async with session_manager.run():
        yield

async def health(request: Request):
    return JSONResponse({"ok": True, "service": "raahi-unified-mcp"})

async def mcp_endpoint(scope, receive, send):
    await session_manager.handle_request(scope, receive, send)

class MCPRoute(BaseRoute):
    def matches(self, scope):
        if scope["type"] == "http" and scope["path"] == "/mcp" and scope["method"] in {"GET", "POST", "DELETE"}:
            return Match.FULL, {}
        return Match.NONE, {}

    async def handle(self, scope, receive, send):
        await mcp_endpoint(scope, receive, send)

from starlette.routing import Match
allowed = [x.strip() for x in os.getenv("ALLOWED_HOSTS", "").split(",") if x.strip()]
middleware = [Middleware(TrustedHostMiddleware, allowed_hosts=allowed or ["*"])]
app = Starlette(routes=[Route("/health", health, methods=["GET"]),
                        MCPRoute()],
                middleware=middleware, lifespan=lifespan)
