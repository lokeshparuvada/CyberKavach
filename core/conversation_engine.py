"""
core/conversation_engine.py
-----------------------------
Channel-agnostic conversational state machine. WhatsApp, IVR, and the
mobile app all drive the SAME engine instance type -- only the transport
(webhook JSON, DTMF/speech, REST calls) differs. This guarantees identical
fraud-detection behaviour and NCRB reporting logic across every channel.

States:
  ASK_LANGUAGE -> AWAIT_INPUT -> SHOW_VERDICT -> ASK_REPORT ->
  COLLECT_REPORT_DETAILS -> DONE
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from core.risk_engine import get_default_engine, RiskVerdict
from languages.translator import (
    translate, translate_signal, band_label, SUPPORTED_LANGUAGES,
)
from reporting.ncrb_reporter import NCRBReporter, infer_category
from reporting.pdf_generator import generate_complaint_pdf


class State(Enum):
    ASK_LANGUAGE = auto()
    AWAIT_INPUT = auto()
    SHOW_VERDICT = auto()
    ASK_REPORT = auto()
    COLLECT_NAME = auto()
    COLLECT_PHONE = auto()
    COLLECT_SUSPECT = auto()
    COLLECT_AMOUNT = auto()
    REPORT_DONE = auto()
    DONE = auto()


@dataclass
class Session:
    session_id: str
    channel: str  # "whatsapp" | "ivr" | "mobile_app"
    language: str = "en"
    state: State = State.ASK_LANGUAGE
    last_verdict: Optional[RiskVerdict] = None
    last_text: Optional[str] = None
    citizen_name: Optional[str] = None
    citizen_phone: Optional[str] = None
    suspect_id: Optional[str] = None
    amount_lost: Optional[float] = None
    last_ref_id: Optional[str] = None
    last_pdf_path: Optional[str] = None


class FraudShieldConversation:
    """One instance per active session; call `handle(user_input)` on each
    inbound message/utterance and send back the returned string(s)."""

    def __init__(self, session: Session):
        self.session = session
        self.engine = get_default_engine()
        self.reporter = NCRBReporter()

    # ---- public entry point -------------------------------------------------
    def start(self) -> str:
        lang_prompt = "Choose your language / अपनी भाषा चुनें: " + ", ".join(
            f"{code}={name}" for code, name in SUPPORTED_LANGUAGES.items()
        )
        return lang_prompt

    def handle(self, user_input: str) -> str:
        s = self.session
        user_input = (user_input or "").strip()

        if s.state == State.ASK_LANGUAGE:
            lang = user_input.lower()
            s.language = lang if lang in SUPPORTED_LANGUAGES else "en"
            s.state = State.AWAIT_INPUT
            return translate("welcome", s.language)

        if s.state == State.AWAIT_INPUT:
            return self._assess(user_input)

        if s.state == State.ASK_REPORT:
            if user_input.lower() in ("yes", "y", "हाँ", "ஆம்", "అవును", "ಹೌದು", "അതെ", "হ্যাঁ", "होय", "હા", "ਹਾਂ", "ହଁ", "হয়"):
                s.state = State.COLLECT_NAME
                return "What is your name? (optional — type 'skip' to skip)"
            else:
                s.state = State.DONE
                return "Okay. Stay safe — you can paste another message anytime to check it."

        if s.state == State.COLLECT_NAME:
            s.citizen_name = None if user_input.lower() == "skip" else user_input
            s.state = State.COLLECT_PHONE
            return "Your phone number? (optional — type 'skip' to skip)"

        if s.state == State.COLLECT_PHONE:
            s.citizen_phone = None if user_input.lower() == "skip" else user_input
            s.state = State.COLLECT_SUSPECT
            return "Suspicious number / UPI ID / handle used against you? (optional — type 'skip')"

        if s.state == State.COLLECT_SUSPECT:
            s.suspect_id = None if user_input.lower() == "skip" else user_input
            s.state = State.COLLECT_AMOUNT
            return "Any amount lost (INR)? Enter number or 'skip'."

        if s.state == State.COLLECT_AMOUNT:
            try:
                s.amount_lost = float(user_input) if user_input.lower() != "skip" else None
            except ValueError:
                s.amount_lost = None
            return self._file_report()

        if s.state in (State.REPORT_DONE, State.DONE):
            # Allow re-checking another message without restarting session
            s.state = State.AWAIT_INPUT
            return self._assess(user_input)

        return "Sorry, something went wrong. Let's start again." 

    # ---- internals -----------------------------------------------------------
    def _assess(self, text: str) -> str:
        s = self.session
        verdict = self.engine.assess(text, channel_hint=s.channel)
        s.last_verdict = verdict
        s.last_text = text
        s.state = State.ASK_REPORT if verdict.band.value in ("HIGH", "CRITICAL", "MEDIUM") else State.DONE

        lines = [
            f"[{band_label(verdict.band.value, s.language)}]  Score: {verdict.score}/100",
        ]
        if verdict.signals:
            lines.append("")
            for sig in verdict.signals:
                lines.append(f"• {translate_signal(sig.id, s.language)}")
        if verdict.ml_probability is not None:
            lines.append("")
            lines.append(f"(AI text model: {round(verdict.ml_probability * 100)}% likely a scam, based on wording patterns)")
        lines.append("")
        localized_action = translate(f"action_{verdict.band.value}", s.language)
        lines.append(f"{translate('action_prefix', s.language)} {localized_action}")

        if s.state == State.ASK_REPORT:
            lines.append("")
            lines.append(translate("ask_report", s.language))
        return "\n".join(lines)

    def _file_report(self) -> str:
        s = self.session
        v = s.last_verdict
        category, subcategory = infer_category([sig.id for sig in v.signals]) if v else ("Other Cyber Crime", "unspecified")
        draft = self.reporter.new_draft(
            channel=s.channel,
            language=s.language,
            incident_text=s.last_text or "",
            risk_score=v.score if v else 0,
            risk_band=v.band.value if v else "LOW",
            category=category,
            subcategory=subcategory,
            citizen_name=s.citizen_name,
            citizen_phone=s.citizen_phone,
            citizen_state=None,
            suspect_number_or_id=s.suspect_id,
            amount_lost=s.amount_lost,
        )
        result = self.reporter.submit(draft)
        pdf_path = generate_complaint_pdf(draft, result)
        s.last_ref_id = result["ref_id"]
        s.last_pdf_path = str(pdf_path)
        s.state = State.REPORT_DONE
        return (
            translate("report_filed", s.language, ref_id=result["ref_id"])
            + f"\nPortal: {result['portal_url']}  |  Helpline: {result['helpline']}"
            + "\nA ready-to-file PDF with every field pre-filled has been prepared for you."
            + "\n\n(Type any new suspicious message to check it, or 'bye' to end.)"
        )
