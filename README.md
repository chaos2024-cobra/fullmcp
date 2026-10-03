# Raahi Unified MCP

Raahi Unified MCP is one Streamable HTTP MCP connector for the
case-competition agent. It keeps provider credentials and integration logic
behind one `/mcp` endpoint; AgenticOrg does not need separate backend
connectors. WhatsApp remains a separate native AgenticOrg connector.

## Architecture and provider boundary

The server uses the MCP Python SDK **2.3.0** low-level server and session
manager and exposes exactly 21 tools. `capabilities.py` is the source of
truth for whether a capability is real, conditional, or mock-only.

Gnani tools use the real API when `GNANI_API_KEY` and `GNANI_BASE_URL` are
configured. Delhivery tools use the real API when its credentials and base URL
are configured. `collect_payment` uses Pine Labs/Plural when its access token
is configured. Pine Labs AA/identity capabilities are conditional: they use a
real configured integration only when credentials are present, otherwise they
return deterministic mock results explicitly marked `execution_mode=mock`.
Appointment booking, travel insurance, web checklists, and the bank-letter
workaround are internal deterministic mocks and are never described as
partner-issued artifacts.

Every result has `ok`, `provider`, `execution_mode`, `tool`, `request_id`,
`data`, and `error`. Configured real-provider failures remain real-provider
errors; there is no silent real-to-mock fallback. Sensitive credentials and
identity/payment data are not logged or accepted as tool inputs where they are
not needed.

## Tools

`transcribe_speech`, `speak_reply`, `call_bank_rm_or_desk`,
`read_call_outcome`, `navigate_ivr`, `pull_case_status_into_call`,
`request_aa_consent`, `fetch_bank_data`, `verify_pan`, `fetch_digilocker_doc`,
`esign_document`, `collect_payment`, `issue_bank_letter`,
`check_serviceability`, `schedule_document_pickup`, `track_shipment`,
`standardise_address`, `estimate_route`, `book_appointment`,
`issue_travel_insurance`, and `create_web_checklist`.

## Safety and state rules

The backend preserves L3 boundaries: upfront consent, authorized data,
confirmed phone numbers, approved document locations, and pre-approved spend.
It does not infer uncertain voice destinations, dates, amounts, or numbers;
guess IVR options; re-request rejected consent; claim an action from a
connected call; charge above the confirmed amount; or treat a payment link as
payment success. A shipment must progress through evidence-backed
`pickup_requested`, `pickup_confirmed`, `picked_up`, `in_transit`, and
`delivered` states. NDR and unknown tracking states can never become
delivered, and payment/document state remains separate.

## Configuration

Copy `.env.example` to `.env` locally. Provider credentials are optional when
running deterministic mocks. `MOCK_MODE` is useful for local tests and
`MOCK_SCENARIO` supports `SUCCESS`, `TIMEOUT`, `DECLINED`, `NDR`,
`UNSERVICEABLE`, `NO_SLOT`, `CONSENT_REJECTED`, and `OTP_FAILURE`. Do not
commit `.env` or put secrets in this README.

## Local run and tests

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000
pytest -q
```

Health is `GET /health`. The MCP endpoint is `/mcp`; initialize with the MCP
client and then call `tools/list`.

## Render and AgenticOrg

`render.yaml` defines the Python service, health check, build command, and
`uvicorn server:app --host 0.0.0.0 --port $PORT` start command. Create a
Render Blueprint from this repository, add provider secrets in Render's
environment settings, and set `MOCK_MODE=false` only after official provider
endpoints and credentials have been verified. No credential is required in
GitHub.

Register the single AgenticOrg MCP URL:

`https://<render-service>.onrender.com/mcp`

## State, chain of custody, and failures

The current case layer is intentionally small and in-memory for the
competition backend. It can be replaced by a persistent store without
changing the MCP contract. Provider timeout, decline, rejected consent,
unserviceable pickup, NDR, OTP failure, and partial/unknown voice outcomes
are explicit errors or statuses. They stop the unsafe branch rather than
fabricating completion.