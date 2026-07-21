"""
reporting/ncrb_reporter.py
----------------------------
Guided complaint builder mirroring the fields required by the National
Cyber Crime Reporting Portal (cybercrime.gov.in) / helpline 1930.

This module does NOT submit to the live government portal (no public
citizen-submission API exists) -- it prepares a complete, correctly
structured complaint packet and reference ID that the citizen can review,
then either:
  (a) auto-fill/paste into the NCRB web form, or
  (b) be read out step-by-step over IVR, or
  (c) be handed to a human agent / partner integration if one is
      contracted (mock `submit()` shows where that call would go).
"""

from __future__ import annotations
import json
import uuid
import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

COMPLAINT_STORE = Path(__file__).parent / "complaints_store.jsonl"


@dataclass
class ComplaintDraft:
    ref_id: str
    created_at: str
    channel: str                      # whatsapp | ivr | mobile_app
    language: str
    category: str                     # e.g. "Financial Fraud", "Impersonation"
    subcategory: str                  # e.g. "digital_arrest_scam"
    incident_text: str                # transcript / message body
    risk_score: int
    risk_band: str
    citizen_name: Optional[str] = None
    citizen_phone: Optional[str] = None
    citizen_state: Optional[str] = None
    suspect_number_or_id: Optional[str] = None
    amount_lost: Optional[float] = None
    evidence_notes: Optional[str] = None
    status: str = "DRAFT"  # DRAFT -> READY_FOR_NCRB -> SUBMITTED (mock)

    def to_ncrb_payload(self) -> dict:
        """Shape data the way the NCRB portal's 'Report Cyber Crime' form
        expects (category -> subcategory -> incident detail -> suspect
        info -> financial loss) so it can be copy-filled or, if a partner
        API becomes available, submitted directly."""
        return {
            "complaintReferenceId": self.ref_id,
            "incidentCategory": self.category,
            "incidentSubCategory": self.subcategory,
            "incidentDateTime": self.created_at,
            "incidentDescription": self.incident_text,
            "complainant": {
                "name": self.citizen_name,
                "mobile": self.citizen_phone,
                "state": self.citizen_state,
                "preferredLanguage": self.language,
            },
            "suspectDetails": {
                "phoneOrHandle": self.suspect_number_or_id,
            },
            "financialLoss": self.amount_lost,
            "systemRiskAssessment": {
                "score": self.risk_score,
                "band": self.risk_band,
            },
            "evidenceNotes": self.evidence_notes,
            "sourceChannel": self.channel,
        }


class NCRBReporter:
    def __init__(self, store_path: Path = COMPLAINT_STORE):
        self.store_path = store_path

    def new_draft(self, *, channel: str, language: str, incident_text: str,
                  risk_score: int, risk_band: str, category: str = "Financial Fraud",
                  subcategory: str = "unspecified", **citizen_fields) -> ComplaintDraft:
        draft = ComplaintDraft(
            ref_id=f"CK-{uuid.uuid4().hex[:10].upper()}",
            created_at=datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            channel=channel,
            language=language,
            category=category,
            subcategory=subcategory,
            incident_text=incident_text,
            risk_score=risk_score,
            risk_band=risk_band,
            **citizen_fields,
        )
        return draft

    def persist(self, draft: ComplaintDraft) -> None:
        """Append-only local store for the demo. Swap for a real DB
        (Postgres/DynamoDB) in production; keep append-only audit trail
        either way for evidentiary integrity."""
        draft.status = "READY_FOR_NCRB"
        with open(self.store_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(draft)) + "\n")

    def submit(self, draft: ComplaintDraft) -> dict:
        """
        Mock submission. In production this is where an authorized
        integration (e.g. via I4C/NCRB API access, if and when granted to
        the implementing agency) would POST `draft.to_ncrb_payload()`.
        Until then this returns guidance for the citizen to file manually
        at cybercrime.gov.in or call 1930, with the pre-filled packet
        attached for quick copy-paste / agent hand-off.
        """
        draft.status = "SUBMITTED_MOCK"
        self.persist(draft)
        return {
            "ref_id": draft.ref_id,
            "status": draft.status,
            "portal_url": "https://cybercrime.gov.in",
            "helpline": "1930",
            "payload": draft.to_ncrb_payload(),
        }


CATEGORY_MAP = {
    "otp_request": ("Financial Fraud", "credential_phishing"),
    "kyc_update_urgent": ("Financial Fraud", "kyc_impersonation"),
    "lottery_prize": ("Financial Fraud", "lottery_advance_fee"),
    "threat_arrest": ("Other Cyber Crime", "digital_arrest_scam"),
    "remote_access": ("Financial Fraud", "remote_access_takeover"),
    "unknown_upi_request": ("Financial Fraud", "upi_collect_fraud"),
    "job_investment_scam": ("Financial Fraud", "investment_job_scam"),
    "bank_impersonation": ("Financial Fraud", "bank_impersonation"),
    "link_click": ("Financial Fraud", "phishing_link"),
}


def infer_category(signal_ids: list[str]) -> tuple[str, str]:
    for sid in signal_ids:
        if sid in CATEGORY_MAP:
            return CATEGORY_MAP[sid]
    return ("Other Cyber Crime", "unspecified")