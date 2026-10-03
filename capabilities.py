"""The single source of truth for the public Raahi tool surface."""

REAL_TOOLS = {
    "transcribe_speech": "gnani", "speak_reply": "gnani",
    "call_bank_rm_or_desk": "gnani", "read_call_outcome": "gnani",
    "navigate_ivr": "gnani", "check_serviceability": "delhivery",
    "schedule_document_pickup": "delhivery", "track_shipment": "delhivery",
    "collect_payment": "pinelabs", "standardise_address": "delhivery",
    "estimate_route": "delhivery",
}
MOCK_ONLY_TOOLS = {
    "issue_bank_letter", "book_appointment", "issue_travel_insurance",
    "create_web_checklist",
}
CONDITIONAL_TOOLS = {
    "request_aa_consent": "pinelabs", "fetch_bank_data": "pinelabs",
    "verify_pan": "pinelabs", "fetch_digilocker_doc": "pinelabs",
    "esign_document": "pinelabs",
}
TOOL_NAMES = [
    "transcribe_speech", "speak_reply", "call_bank_rm_or_desk",
    "read_call_outcome", "navigate_ivr", "pull_case_status_into_call",
    "request_aa_consent", "fetch_bank_data", "verify_pan",
    "fetch_digilocker_doc", "esign_document", "collect_payment",
    "issue_bank_letter", "check_serviceability", "schedule_document_pickup",
    "track_shipment", "standardise_address", "estimate_route",
    "book_appointment", "issue_travel_insurance", "create_web_checklist",
]

def registry():
    result = {}
    for name in TOOL_NAMES:
        if name in MOCK_ONLY_TOOLS:
            provider, mode, supported = "raahi_mock", "mock", True
        elif name == "pull_case_status_into_call":
            provider, mode, supported = "internal_workflow", "mock", True
        elif name in CONDITIONAL_TOOLS:
            provider, mode, supported = CONDITIONAL_TOOLS[name], "conditional", True
        else:
            provider, mode, supported = REAL_TOOLS[name], "real", True
        result[name] = {"name": name, "provider": provider,
                        "execution_mode": mode, "supported": supported,
                        "requires_credentials": mode in ("real", "conditional"),
                        "failure_modes": ["TIMEOUT", "INVALID_REQUEST"]}
    return result
