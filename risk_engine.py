"""
core/risk_engine.py
--------------------
Real-time fraud risk assessment engine for Citizen Fraud Shield.

Design:
- Rule/pattern based scoring (fast, explainable, no external ML dependency
  needed at hackathon/demo stage). Structured so a trained classifier can be
  swapped in later via the `MLRiskModel` stub without touching callers.
- Works on any free text: call transcript, SMS/WhatsApp message body, or a
  structured payment-request description.
- Produces a RiskVerdict with a 0-100 score, band (LOW/MEDIUM/HIGH/CRITICAL),
  matched signals, and a plain-language explanation (English keys; caller
  translates via languages.translator).
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict


class RiskBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class Signal:
    id: str
    description: str
    weight: int
    category: str


@dataclass
class RiskVerdict:
    score: int
    band: RiskBand
    signals: List[Signal] = field(default_factory=list)
    recommended_action: str = ""
    ml_probability: float | None = None   # adaptive classifier's scam probability, 0-1
    ml_label: str | None = None            # "likely_scam" | "likely_safe" | "uncertain"

    def explanation_keys(self) -> List[str]:
        """Return signal ids so the language layer can localize each one."""
        return [s.id for s in self.signals]


# ---------------------------------------------------------------------------
# Signal library: (id, regex/keyword list, weight, category)
# Weights are additive; tuned so 2-3 strong signals already cross HIGH.
# Extend this table freely -- it is the single source of truth for detection.
# ---------------------------------------------------------------------------
SIGNAL_LIBRARY: List[Dict] = [
    dict(id="otp_request", category="credential_theft", weight=35,
         patterns=[r"\botp\b", r"one[\s-]?time[\s-]?password", r"share.*(code|pin)"]),
    dict(id="kyc_update_urgent", category="impersonation", weight=30,
         patterns=[r"kyc", r"account.*(block|suspend|freeze)", r"update.*(pan|aadhaar|aadhar)"]),
    dict(id="lottery_prize", category="advance_fee", weight=30,
         patterns=[r"lottery", r"lucky\s?draw", r"you\s?have\s?won", r"prize\s?money"]),
    dict(id="urgent_payment", category="pressure_tactics", weight=25,
         patterns=[r"pay.*(immediately|urgently|now)", r"processing\s?fee", r"advance\s?fee",
                    r"customs\s?duty", r"courier.*(parcel|package).*fee"]),
    dict(id="threat_arrest", category="digital_arrest_scam", weight=40,
         patterns=[r"arrest\s?warrant", r"police\s?action", r"cbi", r"ed\s?officer",
                    r"digital\s?arrest", r"legal\s?action.*(immediately|today)"]),
    dict(id="remote_access", category="account_takeover", weight=35,
         patterns=[r"anydesk", r"teamviewer", r"screen\s?share", r"remote\s?access", r"quicksupport"]),
    dict(id="unknown_upi_request", category="payment_fraud", weight=25,
         patterns=[r"upi\s?pin", r"collect\s?request", r"scan.*(qr|code).*receive", r"send\s?money.*refund"]),
    dict(id="job_investment_scam", category="advance_fee", weight=20,
         patterns=[r"work\s?from\s?home", r"guaranteed\s?returns", r"double\s?your\s?money",
                    r"investment.*(scheme|opportunity).*guarantee", r"part\s?time\s?job.*earn"]),
    dict(id="bank_impersonation", category="impersonation", weight=25,
         patterns=[r"bank.*(officer|executive|representative)", r"rbi\s?(official|guideline)",
                    r"debit\s?card.*(block|expire)"]),
    dict(id="link_click", category="phishing", weight=20,
         patterns=[r"https?://\S+", r"click.*(link|here)", r"bit\.ly", r"tinyurl"]),
    dict(id="secrecy_pressure", category="pressure_tactics", weight=15,
         patterns=[r"do\s?not\s?tell", r"keep.*confidential", r"don'?t\s?inform\s?(family|bank)"]),
    dict(id="unknown_number_pattern", category="spoofing", weight=10,
         patterns=[r"\+92", r"\+1\s?\(?", r"international\s?number"]),
]

_COMPILED = [
    {**s, "regex": re.compile("|".join(s["patterns"]), re.IGNORECASE)}
    for s in SIGNAL_LIBRARY
]


def _band_for_score(score: int) -> RiskBand:
    if score >= 70:
        return RiskBand.CRITICAL
    if score >= 45:
        return RiskBand.HIGH
    if score >= 20:
        return RiskBand.MEDIUM
    return RiskBand.LOW


_ACTION_BY_BAND = {
    RiskBand.CRITICAL: "STOP. Do not pay or share any code. Hang up / do not reply. "
                        "We recommend filing an NCRB report immediately.",
    RiskBand.HIGH: "This is very likely a scam. Do not share OTP, PIN, or make any payment. "
                    "Verify independently using an official number before acting.",
    RiskBand.MEDIUM: "This shows some suspicious signs. Proceed with caution and verify "
                       "the sender/caller through an official channel before responding.",
    RiskBand.LOW: "No strong fraud indicators found, but stay alert with unknown contacts.",
}


class RuleBasedRiskEngine:
    """Deterministic, explainable fraud scorer. Default engine for the MVP."""

    def assess(self, text: str, channel_hint: str | None = None) -> RiskVerdict:
        text = text or ""
        matched: List[Signal] = []
        score = 0
        for s in _COMPILED:
            if s["regex"].search(text):
                matched.append(Signal(id=s["id"], description=s["id"].replace("_", " "),
                                       weight=s["weight"], category=s["category"]))
                score += s["weight"]

        # Compounding: multiple pressure/impersonation signals together are
        # disproportionately dangerous (classic scam script combo).
        categories = {m.category for m in matched}
        if {"impersonation", "pressure_tactics"} <= categories:
            score += 10
        if {"credential_theft", "remote_access"} <= categories:
            score += 15

        score = min(score, 100)
        band = _band_for_score(score)
        return RiskVerdict(
            score=score,
            band=band,
            signals=matched,
            recommended_action=_ACTION_BY_BAND[band],
        )


class HybridRiskEngine:
    """
    Default engine for the app. Keeps the rule engine as the primary,
    explainable scorer (every point traces to a named signal a citizen can
    read and understand), and blends in a live-learning ML classifier
    (core/ml_risk_model.py) that catches rephrased scams the fixed regex
    library hasn't seen yet.

    The ML signal can only nudge the score by up to +/-15 points and can
    never flip a LOW straight to CRITICAL on its own -- this keeps a
    single noisy prediction from dominating the verdict, and keeps the
    system auditable: the rule signals always explain the bulk of the
    score, and the ML opinion is surfaced to callers as its own labeled
    field (see RiskVerdict.ml_probability) rather than hidden inside a
    black-box number.
    """

    def __init__(self):
        self.rule_engine = RuleBasedRiskEngine()
        self._ml = None  # lazy-loaded: sklearn import + model load is not free

    def _ml_classifier(self):
        if self._ml is None:
            from core.ml_risk_model import get_classifier
            self._ml = get_classifier()
        return self._ml

    def assess(self, text: str, channel_hint: str | None = None) -> RiskVerdict:
        verdict = self.rule_engine.assess(text, channel_hint=channel_hint)
        try:
            prediction = self._ml_classifier().predict(text)
        except Exception:
            verdict.ml_probability = None
            verdict.ml_label = None
            return verdict

        nudge = round((prediction.scam_probability - 0.5) * 30)  # -15..+15
        adjusted_score = max(0, min(100, verdict.score + nudge))
        if adjusted_score != verdict.score:
            verdict.score = adjusted_score
            verdict.band = _band_for_score(adjusted_score)
            verdict.recommended_action = _ACTION_BY_BAND[verdict.band]
        verdict.ml_probability = prediction.scam_probability
        verdict.ml_label = prediction.label
        return verdict

    def record_feedback(self, text: str, is_scam: bool, source: str = "citizen") -> None:
        self._ml_classifier().record_feedback(text, is_scam, source=source)

    def ml_status(self) -> dict:
        return self._ml_classifier().status()


def get_default_engine() -> HybridRiskEngine:
    return HybridRiskEngine()


if __name__ == "__main__":
    engine = get_default_engine()
    samples = [
        "Sir this is CBI officer speaking, there is a digital arrest warrant against you, "
        "install AnyDesk immediately and pay processing fee or you will be arrested today.",
        "Hi, are we still on for lunch tomorrow?",
        "Your bank account will be blocked, update your KYC by sharing OTP sent to your phone now.",
    ]
    for s in samples:
        v = engine.assess(s)
        print(f"\nTEXT: {s[:60]}...")
        print(f"SCORE: {v.score} | BAND: {v.band.value}")
        print(f"SIGNALS: {[sig.id for sig in v.signals]}")
        print(f"ACTION: {v.recommended_action}")