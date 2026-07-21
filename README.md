# Cyber Kavach (Multi-channel)

A conversational AI system that walks citizens through real-time fraud risk
assessment for suspicious calls, messages, or payment requests — with
instant verdicts, guided NCRB reporting, and advisory in 12 regional
Indian languages. Accessible via WhatsApp, IVR (voice call), and a mobile
app REST API.

## Why this architecture

One shared brain, three thin channel adapters:

```
                     ┌─────────────────────────┐
  WhatsApp  ───────▶ │                         │
  (Flask webhook)    │   core/risk_engine.py    │  rule-based fraud
                     │   (fraud scoring)        │  scoring, 12 signal
  IVR (voice) ─────▶ │                         │  categories
  (Flask + TwiML)    │   core/conversation_     │
                     │   engine.py              │  channel-agnostic
  Mobile App ──────▶ │   (state machine)        │  state machine
  (FastAPI REST)     │                         │
                     │   languages/translator.py│  12 languages,
                     │                         │  static + pluggable MT
                     │   reporting/ncrb_        │
                     │   reporter.py            │  guided NCRB
                     └─────────────────────────┘  complaint builder
```

Every channel calls the **same** `FraudShieldConversation` state machine,
so a WhatsApp user, an IVR caller, and a mobile app user get identical
fraud-detection logic, identical NCRB complaint quality, and identical
language coverage — only the transport differs.

## Project layout

```
cyberkavach/
├── core/
│   ├── risk_engine.py          # rule-based fraud scorer (12 signal categories)
│   └── conversation_engine.py  # channel-agnostic state machine
├── languages/
│   └── translator.py           # 12-language strings + signal explanations
├── reporting/
│   └── ncrb_reporter.py        # guided NCRB complaint draft + payload
├── channels/
│   ├── whatsapp_channel.py     # Flask webhook (Twilio WhatsApp API-compatible)
│   ├── ivr_channel.py          # Flask + TwiML voice (DTMF + speech)
│   └── mobile_api.py           # FastAPI REST backend for the mobile app
├── demo_cli.py                 # interactive terminal demo (no deps needed)
├── test_scripted_demo.py       # scripted end-to-end walkthrough
└── requirements.txt
```

## Quick start (zero external services)

```bash
python demo_cli.py              # interactive
python test_scripted_demo.py    # scripted walkthrough, prints every turn
```

## Run the mobile API

```bash
pip install -r requirements.txt
uvicorn channels.mobile_api:app --reload --port 5003
```

```bash
# One-shot check (no session, good for a "paste & check" widget)
curl -X POST localhost:5003/quick-check \
  -H "Content-Type: application/json" \
  -d '{"text":"Share your OTP to update KYC or account will be blocked","language":"hi"}'

# Full guided session (mirrors the WhatsApp/IVR flow)
curl -X POST localhost:5003/session/start -d '{"language":"en"}' -H "Content-Type: application/json"
curl -X POST localhost:5003/session/<id>/message -d '{"text":"..."}' -H "Content-Type: application/json"
```

## Run the WhatsApp webhook

```bash
python channels/whatsapp_channel.py   # listens on :5001
# Point a Twilio WhatsApp Sandbox (or Meta Cloud API) webhook to
# POST https://<your-host>/whatsapp/webhook
```

## Run the IVR webhook

```bash
python channels/ivr_channel.py        # listens on :5002
# Point a Twilio Voice number's webhook to POST https://<your-host>/ivr/voice
# Caller selects language by DTMF digit, then describes the incident by
# speech (Twilio speech-to-text); verdict is read back by <Say> in the
# same language.
```

## How fraud scoring works (`core/risk_engine.py`)

Deterministic, explainable, rule-based scorer — deliberately chosen over a
black-box model for an MVP/hackathon stage because:
- **No training data needed** to launch.
- **Fully explainable** — every point of the score maps to a named signal
  (OTP request, digital-arrest threat, remote-access app, UPI collect
  request, etc.), which the UI shows back to the citizen.
- **Fast and offline-capable** — matters for IVR/low-connectivity users.

12 signal categories are matched against the transcript/message text via
regex, with compounding bonus points when dangerous combinations appear
together (e.g. impersonation + pressure tactics, or credential-theft +
remote-access — the classic "digital arrest" scam combo). Score maps to
LOW / MEDIUM / HIGH / CRITICAL bands, each with a specific recommended
action.

`MLRiskModel` is stubbed with the same interface so a fine-tuned
classifier (e.g. IndicBERT trained on labeled scam transcripts) can later
replace or ensemble with the rule engine without touching any caller code.

## Language coverage (`languages/translator.py`)

English, Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, Marathi,
Gujarati, Punjabi, Odia, Assamese — all fixed conversational strings and
every risk-signal explanation are pre-translated (offline, zero-latency,
works over IVR with no network dependency for the built-in phrases).
`translate_dynamic()` is a pluggable hook to call an external MT service
(e.g. Bhashini, purpose-built for Indian languages) for free-text the
citizen types themselves, such as a complaint narrative.

# Fonts needed here

`reporting/pdf_generator.py` looks for these exact filenames in this
folder to render non-Latin scripts correctly in generated complaint
PDFs. Without them, it falls back to ASCII-safe Helvetica and logs a
warning — nothing crashes, but Tamil/Hindi/etc. text gets stripped.

| Language  | Required filename(s) |
|-----------|------------------------|
| Tamil     | `NotoSansTamil-Regular.ttf`, `NotoSansTamil-Bold.ttf` |
| Hindi     | `NotoSansDevanagari-Regular.ttf` |
| Telugu    | `NotoSansTelugu-Regular.ttf` |
| Kannada   | `NotoSansKannada-Regular.ttf` |
| Malayalam | `NotoSansMalayalam-Regular.ttf` |
| Bengali   | `NotoSansBengali-Regular.ttf` |

## Option A — automatic (recommended)

```bash
cd reporting/fonts
python download_fonts.py          # all 6 languages
python download_fonts.py ta hi    # just Tamil + Hindi
```

## Option B — manual

1. Go to [fonts.google.com/noto](https://fonts.google.com/noto), search
   the script name (e.g. "Noto Sans Tamil"), click it, **Download family**.
2. Unzip, grab the **static** `Regular` (and `Bold`) `.ttf` — not the
   variable-font one.
3. Rename to match the table above exactly (case-sensitive) and drop
   into this folder.

Either way, no code changes needed — `pdf_generator.py` re-checks this
folder every time it builds a PDF.

## NCRB reporting (`reporting/ncrb_reporter.py`)

Builds a complete complaint packet shaped like the National Cyber Crime
Reporting Portal's (cybercrime.gov.in) "Report Cyber Crime" form —
category, subcategory, incident description, suspect details, financial
loss, risk assessment. There is no public citizen-submission API for
NCRB, so this module prepares the packet and a reference ID for the
citizen to copy into the portal or read out over a human-agent handoff;
`submit()` is where a direct API integration would plug in if the
implementing agency is granted access (e.g. via I4C).

## Extending this MVP

- **Swap the risk engine**: implement `MLRiskModel.assess()` with a
  trained classifier; it already matches the `RuleBasedRiskEngine`
  interface used everywhere else.
- **Durable sessions**: replace the in-memory `_SESSIONS` dicts in each
  channel adapter with Redis/DynamoDB for multi-instance deployments.
- **Voice quality**: swap Twilio `<Say>` for higher-quality regional TTS
  (e.g. Bhashini TTS) if available.
- **Direct NCRB submission**: wire `NCRBReporter.submit()` to a real
  government API once integration access is granted.

# Progressive Web App (PWA)

The frontend is a fully installable Progressive Web App.

### Run locally

```bash
cd frontend
python -m http.server 8080
```

Open

http://localhost:8080

Chrome/Edge will automatically detect the PWA.

---

### Install

Desktop

• Click Install App in Chrome

or

• Click the install icon in the address bar.

Android

Open Chrome

↓

Open the website

↓

Add to Home Screen

↓

Install

The app now launches like a native application and works offline using the Service Worker.

---

## Packaging as Android / iOS App

### Option 1 — Capacitor

```
npm install @capacitor/core
npm install @capacitor/cli

npx cap init

npx cap add android

npx cap copy

npx cap open android
```

Build using Android Studio.

---

### Option 2 — PWABuilder

https://www.pwabuilder.com

Enter your deployed website URL.

PWABuilder generates:

- Android APK
- Android App Bundle (AAB)
- Windows package
- iOS project

ready for publishing.
**Authors**-Team FOUR
- Lokesh Naidu Paruvada
- Golconda Sai Rithvik
- Aangara Satya Sesha Sri Chaitanya
- Mudavath Praveen Kumar
