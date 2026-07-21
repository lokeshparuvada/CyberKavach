"""
channels/ivr_channel.py
--------------------------
IVR adapter for feature-phone / voice-call access (critical for rural and
elderly citizens who cannot use WhatsApp or an app). Built against Twilio
Voice's <Gather> verb (DTMF + speech-to-text), returning TwiML. The same
pattern maps directly onto Asterisk/FreeSWITCH AGI or Exotel if a
telco-hosted deployment is preferred for an India rollout.

Call flow:
  1. Caller dials the shield number.
  2. IVR asks for language via DTMF digits (1=Hindi, 2=Tamil, ... 0=English).
  3. Caller is asked to describe the call/message using speech-to-text
     (Twilio <Gather input="speech">), or press a digit to be transferred
     to a human agent (fallback for anyone who prefers/needs it).
  4. Verdict + action is read back using <Say> in the chosen language voice.
  5. Caller can press 1 to file an NCRB report; guided by further prompts.

Run:
    pip install flask
    python channels/ivr_channel.py
Point your Twilio Voice number's webhook to:
    POST https://<your-host>/ivr/voice
"""

from __future__ import annotations
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, Response

from core.conversation_engine import FraudShieldConversation, Session, State

app = Flask(__name__)
_SESSIONS: dict[str, FraudShieldConversation] = {}

# DTMF digit -> language code, read out on the initial menu
DTMF_LANGUAGE_MAP = {
    "0": "en", "1": "hi", "2": "ta", "3": "te", "4": "kn", "5": "ml",
    "6": "bn", "7": "mr", "8": "gu", "9": "pa",
}

# Twilio <Say> language codes closest to each supported language for TTS.
TTS_VOICE = {
    "en": "en-IN", "hi": "hi-IN", "ta": "ta-IN", "te": "te-IN",
    "kn": "kn-IN", "ml": "ml-IN", "bn": "bn-IN", "mr": "mr-IN",
    "gu": "gu-IN", "pa": "pa-IN", "or": "or-IN", "as": "as-IN",
}


def _twiml(*parts: str) -> Response:
    body = "".join(parts)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', mimetype="text/xml")


def _say(text: str, lang: str) -> str:
    voice_lang = TTS_VOICE.get(lang, "en-IN")
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<Say language="{voice_lang}">{escaped}</Say>'


@app.route("/ivr/voice", methods=["POST"])
def ivr_entry():
    """First hit when the call connects: ask for language via DTMF."""
    menu = (
        "Welcome to Citizen Fraud Shield. "
        "Press 0 for English, 1 for Hindi, 2 for Tamil, 3 for Telugu, "
        "4 for Kannada, 5 for Malayalam, 6 for Bengali, 7 for Marathi, "
        "8 for Gujarati, 9 for Punjabi."
    )
    gather = (
        f'<Gather numDigits="1" action="/ivr/language" method="POST" timeout="8">'
        f'{_say(menu, "en")}'
        f'</Gather>'
        f'{_say("We did not receive a selection. Goodbye.", "en")}'
    )
    return _twiml(gather)


@app.route("/ivr/language", methods=["POST"])
def ivr_language():
    call_sid = request.values.get("CallSid", "unknown")
    digit = request.values.get("Digits", "0")
    lang = DTMF_LANGUAGE_MAP.get(digit, "en")

    session = Session(session_id=call_sid, channel="ivr", language=lang)
    _SESSIONS[call_sid] = FraudShieldConversation(session)
    _SESSIONS[call_sid].session.state = State.AWAIT_INPUT

    prompt = "Please describe the call or message that seemed suspicious, after the beep."
    gather = (
        f'<Gather input="speech" action="/ivr/capture" method="POST" '
        f'speechTimeout="auto" language="{TTS_VOICE.get(lang, "en-IN")}">'
        f'{_say(prompt, lang)}'
        f'</Gather>'
    )
    return _twiml(gather)


@app.route("/ivr/capture", methods=["POST"])
def ivr_capture():
    call_sid = request.values.get("CallSid", "unknown")
    speech_text = request.values.get("SpeechResult", "")
    convo = _SESSIONS.get(call_sid)
    if convo is None:
        return _twiml(_say("Session expired. Please call again.", "en"))

    reply_text = convo.handle(speech_text)
    lang = convo.session.language

    if convo.session.state.name == "ASK_REPORT":
        gather = (
            f'<Gather numDigits="1" action="/ivr/report_choice" method="POST" timeout="8">'
            f'{_say(reply_text, lang)}'
            f'{_say("Press 1 for yes, 2 for no.", lang)}'
            f'</Gather>'
        )
        return _twiml(gather)

    return _twiml(_say(reply_text, lang), _say("Thank you for using Citizen Fraud Shield. Goodbye.", lang))


@app.route("/ivr/report_choice", methods=["POST"])
def ivr_report_choice():
    call_sid = request.values.get("CallSid", "unknown")
    digit = request.values.get("Digits", "2")
    convo = _SESSIONS.get(call_sid)
    if convo is None:
        return _twiml(_say("Session expired.", "en"))

    lang = convo.session.language
    reply_text = convo.handle("yes" if digit == "1" else "no")

    if digit == "1":
        # Continue collecting details via successive speech gathers
        gather = (
            f'<Gather input="speech" action="/ivr/detail" method="POST" '
            f'speechTimeout="auto" language="{TTS_VOICE.get(lang, "en-IN")}">'
            f'{_say(reply_text, lang)}'
            f'</Gather>'
        )
        return _twiml(gather)

    return _twiml(_say(reply_text, lang), _say("Goodbye.", lang))


@app.route("/ivr/detail", methods=["POST"])
def ivr_detail():
    """Generic step used repeatedly while conversation_engine walks through
    COLLECT_NAME -> COLLECT_PHONE -> COLLECT_SUSPECT -> COLLECT_AMOUNT."""
    call_sid = request.values.get("CallSid", "unknown")
    speech_text = request.values.get("SpeechResult", "skip")
    convo = _SESSIONS.get(call_sid)
    if convo is None:
        return _twiml(_say("Session expired.", "en"))

    lang = convo.session.language
    reply_text = convo.handle(speech_text)

    if convo.session.state.name in ("COLLECT_PHONE", "COLLECT_SUSPECT", "COLLECT_AMOUNT"):
        gather = (
            f'<Gather input="speech" action="/ivr/detail" method="POST" '
            f'speechTimeout="auto" language="{TTS_VOICE.get(lang, "en-IN")}">'
            f'{_say(reply_text, lang)}'
            f'</Gather>'
        )
        return _twiml(gather)

    return _twiml(_say(reply_text, lang), _say("Thank you. Goodbye.", lang))


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "service": "ivr_channel"}


if __name__ == "__main__":
    app.run(port=5002, debug=True)
