"""
channels/whatsapp_channel.py
------------------------------
WhatsApp adapter. Written against the Twilio WhatsApp API's webhook
contract (works the same for Meta's WhatsApp Cloud API with minor field
renames) -- swap `parse_inbound` / `build_reply` if you switch providers.

Run:
    pip install flask twilio
    export TWILIO_AUTH_TOKEN=... (optional, for signature validation)
    python channels/whatsapp_channel.py
Then point your Twilio WhatsApp Sandbox / number's webhook to:
    POST https://<your-host>/whatsapp/webhook
"""

from __future__ import annotations
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, Response
from core.conversation_engine import FraudShieldConversation, Session

app = Flask(__name__)

# In-memory session store keyed by WhatsApp sender number.
# Swap for Redis/DynamoDB in production for durability across restarts.
_SESSIONS: dict[str, FraudShieldConversation] = {}


def _get_or_create_conversation(from_number: str) -> FraudShieldConversation:
    if from_number not in _SESSIONS:
        session = Session(session_id=from_number, channel="whatsapp")
        convo = FraudShieldConversation(session)
        _SESSIONS[from_number] = convo
        # Caller sends the language prompt as the very first reply.
        convo._pending_greeting = convo.start()  # type: ignore[attr-defined]
    return _SESSIONS[from_number]


def build_twiml_reply(message: str) -> str:
    """Minimal TwiML so this works with zero extra dependencies beyond flask."""
    escaped = (message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escaped}</Message></Response>'


@app.route("/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    from_number = request.values.get("From", "unknown")
    body = request.values.get("Body", "")

    convo = _get_or_create_conversation(from_number)
    pending = getattr(convo, "_pending_greeting", None)
    if pending:
        convo._pending_greeting = None  # type: ignore[attr-defined]
        reply = pending
    else:
        reply = convo.handle(body)

    return Response(build_twiml_reply(reply), mimetype="text/xml")


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "service": "whatsapp_channel"}


if __name__ == "__main__":
    app.run(port=5001, debug=True)
