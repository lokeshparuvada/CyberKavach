"""
channels/mobile_api.py
-------------------------
REST API backing the Citizen Fraud Shield mobile app / PWA. Same
conversation engine as WhatsApp/IVR, exposed as clean JSON endpoints.

Run:
    pip install -r requirements.txt
    uvicorn channels.mobile_api:app --reload --port 5003

Example:
    curl -X POST localhost:5003/session/start -d '{"language":"hi"}' -H "Content-Type: application/json"
    curl -X POST localhost:5003/session/{id}/message -d '{"text":"..."}' -H "Content-Type: application/json"
"""

from __future__ import annotations
import os
import sys
import uuid
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.conversation_engine import FraudShieldConversation, Session, State
from core.risk_engine import get_default_engine
from languages.translator import SUPPORTED_LANGUAGES, translate, translate_signal, band_label
from reporting.ncrb_reporter import COMPLAINT_STORE

app = FastAPI(title="Citizen Fraud Shield API", version="1.1")

# Allow the frontend (any origin during dev/demo; lock this down to your
# deployed frontend's exact origin before sharing the link widely).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_SESSIONS: dict[str, FraudShieldConversation] = {}
_engine = get_default_engine()


class StartSessionRequest(BaseModel):
    language: str = "en"


class MessageRequest(BaseModel):
    text: str


class QuickCheckRequest(BaseModel):
    text: str
    language: str = "en"


class FeedbackRequest(BaseModel):
    text: str
    is_scam: bool
    source: str = "citizen"  # "citizen" | "admin"

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Citizen Fraud Shield",
        "version": "1.0",
    }

@app.get("/languages")
def list_languages():
    return SUPPORTED_LANGUAGES


@app.post("/session/start")
def start_session(req: StartSessionRequest):
    session_id = str(uuid.uuid4())
    lang = req.language if req.language in SUPPORTED_LANGUAGES else "en"
    session = Session(session_id=session_id, channel="mobile_app", language=lang)
    convo = FraudShieldConversation(session)
    convo.session.state = State.AWAIT_INPUT
    _SESSIONS[session_id] = convo
    return {"session_id": session_id, "message": translate("welcome", lang)}


@app.post("/session/{session_id}/message")
def send_message(session_id: str, req: MessageRequest):
    convo = _SESSIONS.get(session_id)
    if convo is None:
        raise HTTPException(status_code=404, detail="Session not found. Call /session/start first.")
    reply = convo.handle(req.text)
    s = convo.session
    return {
        "session_id": session_id,
        "state": s.state.name,
        "reply": reply,
        "risk_score": s.last_verdict.score if s.last_verdict else None,
        "risk_band": s.last_verdict.band.value if s.last_verdict else None,
        "ml_probability": s.last_verdict.ml_probability if s.last_verdict else None,
        "ref_id": s.last_ref_id,
        "pdf_url": f"/report/{s.last_ref_id}.pdf" if s.last_ref_id else None,
    }


@app.post("/quick-check")
def quick_check(req: QuickCheckRequest):
    """Stateless one-shot endpoint: no session needed, just score the text.
    Useful for an in-app 'paste and check' widget or share-sheet integration."""
    verdict = _engine.assess(req.text)
    return {
        "score": verdict.score,
        "band": verdict.band.value,
        "band_label": band_label(verdict.band.value, req.language),
        "signals": [
            {"id": s.id, "explanation": translate_signal(s.id, req.language)}
            for s in verdict.signals
        ],
        "recommended_action": translate(f"action_{verdict.band.value}", req.language),
        "ml_probability": verdict.ml_probability,
        "ml_label": verdict.ml_label,
    }


@app.post("/feedback")
def submit_feedback(req: FeedbackRequest):
    """This is what makes the risk engine adaptive rather than a static
    demo: a citizen or admin corrects a verdict ('this actually was/wasn't
    a scam') and the ML layer immediately retrains on it via partial_fit,
    persisted to disk so the correction survives a restart."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    _engine.record_feedback(req.text, req.is_scam, source=req.source)
    return {"status": "learned", "ml_status": _engine.ml_status()}


@app.get("/admin/ml-status")
def ml_status():
    return _engine.ml_status()


@app.post("/admin/ml-retrain")
def ml_retrain():
    """Full refit from seed data + every feedback example collected so
    far -- lets brand-new vocabulary from feedback join the model, not
    just nudge the existing one. Safe to call anytime (e.g. a nightly job)."""
    from core.ml_risk_model import get_classifier
    n = get_classifier().retrain_from_log()
    return {"status": "retrained", "trained_on_examples": n}


@app.get("/report/{ref_id}.pdf")
def download_report_pdf(ref_id: str):
    from reporting.pdf_generator import PDF_OUTPUT_DIR
    path = PDF_OUTPUT_DIR / f"{ref_id}.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No PDF found for that reference ID.")
    return FileResponse(str(path), media_type="application/pdf", filename=f"{ref_id}.pdf")


@app.get("/health")
def health():
    return {"status": "ok", "service": "mobile_api"}


# ---------------------------------------------------------------------------
# Admin/demo endpoints: read back what's been filed to complaints_store.jsonl
# No auth on these for the demo. Add an API-key check before sharing widely.
# ---------------------------------------------------------------------------
def _read_complaints() -> list[dict]:
    if not COMPLAINT_STORE.exists():
        return []
    rows = []
    with open(COMPLAINT_STORE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


@app.get("/admin/stats")
def admin_stats():
    rows = _read_complaints()
    band_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    total_amount_lost = 0.0
    for r in rows:
        band_counts[r["risk_band"]] = band_counts.get(r["risk_band"], 0) + 1
        category_counts[r["subcategory"]] = category_counts.get(r["subcategory"], 0) + 1
        if r.get("amount_lost"):
            total_amount_lost += r["amount_lost"]
    return {
        "total_reports": len(rows),
        "by_band": band_counts,
        "by_subcategory": category_counts,
        "total_amount_lost": total_amount_lost,
    }


@app.get("/admin/complaints")
def admin_complaints(limit: int = 50, offset: int = 0):
    rows = _read_complaints()
    rows_sorted = list(reversed(rows))  # most recent first
    return {
        "total": len(rows_sorted),
        "results": rows_sorted[offset: offset + limit],
    }
