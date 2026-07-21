# Running the website — quick reference

## What changed from the original repo
- Files reorganized into proper packages: `core/`, `languages/`, `reporting/`,
  `channels/` (each with `__init__.py`). The imports in the channel files
  already expected this layout — it just didn't exist before.
- `channels/mobile_api.py`: added CORS so a browser frontend can call it,
  removed dead `__import__` hacks, added two new endpoints:
  - `GET /admin/stats` — totals by risk band, category, amount lost
  - `GET /admin/complaints?limit=&offset=` — list of filed NCRB drafts
- New `frontend/index.html` — a single self-contained file (no npm, no build
  step). Three tabs: Quick Check, Guided Chat, Reports Dashboard.

## 1. Start the backend

```bash
cd citizen_cyber_kavach
pip install -r requirements.txt
python -m uvicorn channels.mobile_api:app --reload --port 5003
```

Confirm it's up: open `http://localhost:5003/health` — should show
`{"status":"ok","service":"mobile_api"}`. You can also open
`http://localhost:5003/docs` for a live API explorer.

## 2. Start the frontend

Just open the file directly, or serve it (recommended, avoids some browser
file:// quirks):

```bash
cd citizen_cyber_kavach/frontend
python3 -m http.server 8080
```

Then visit `http://localhost:8080`.

If your backend is NOT on `localhost:5003` (e.g. you deploy it), open
`frontend/index.html` and change this one line near the top of the
`<script>` block:

```js
const API_BASE = window.API_BASE || "http://localhost:5003";
```

## 3. Deploying for the demo (so it works off your laptop / on a projector)

**Backend** — easiest options for a same-day deploy:
- [Render.com](https://render.com) — "New Web Service", connect repo,
  build command `pip install -r requirements.txt`, start command
  `uvicorn channels.mobile_api:app --host 0.0.0.0 --port $PORT`. Free tier
  works but cold-starts after inactivity — hit `/health` a minute before
  you demo to wake it up.
- [Railway.app](https://railway.app) — similar, often faster cold starts.

**Frontend** — since it's a single static HTML file:
- [Netlify Drop](https://app.netlify.com/drop) — drag the `frontend/`
  folder in, get a URL in ~10 seconds. This is the fastest option.
- Or GitHub Pages if your repo is already on GitHub.

Once backend is deployed, update `API_BASE` in `index.html` to the deployed
backend URL before you drop the frontend folder onto Netlify.

## 4. Demo script (suggested order)

1. **Quick Check tab** — paste the "digital arrest" sample message, show the
   CRITICAL verdict, matched signals, and recommended action. Switch
   language dropdown to Hindi/Tamil and re-run to show localization.
2. **Guided Chat tab** — walk through a full conversation: describe a scam →
   see verdict → say yes to filing a report → fill name/phone/suspect/amount
   → get a reference ID. This is the "full product" moment.
3. **Reports Dashboard tab** — show the report you just filed appearing in
   the aggregate stats. This demonstrates the system isn't just a one-off
   checker — it's building a usable fraud intelligence trail.
4. Mention (don't need to demo) that the exact same `core/` engine also
   powers a WhatsApp bot and an IVR/voice line — same fraud logic, same
   NCRB reporting, three channels, one brain. Point at the architecture
   diagram in `README.md` if asked.

## 4b. Tamil / regional-script PDF rendering — one setup step

`reporting/pdf_generator.py` already contains full Unicode font-loading
logic (registers a TTF with ReportLab, falls back to a Pillow+RAQM
rasterized text block for scripts ReportLab can't shape correctly, and
degrades to ASCII-safe Helvetica with a logged warning if no font is
found). The actual font **files** aren't bundled in this zip (licensing/
size), so on a fresh checkout you'll see a console warning and Tamil/
Hindi/etc. text will fall back to stripped ASCII in generated PDFs until
you add them. Two ways to fix that — see `reporting/fonts/README.md`
for full detail:

```bash
# Option A — automatic (needs internet access; run once)
cd reporting/fonts
python download_fonts.py

# Option B — manual: download from fonts.google.com/noto and drop the
# static Regular/Bold .ttf files (renamed to match reporting/fonts/README.md)
# into reporting/fonts/ yourself.
```

Once matching TTFs are in `reporting/fonts/`, the PDF generator picks
them up automatically on the next run — no code changes needed.

## 5. If something breaks 20 minutes before the demo
- `/health` returns nothing → backend isn't running or wrong port. Restart
  uvicorn.
- Quick check spins forever → open browser dev tools (F12) → Console tab,
  check for a CORS or connection error, confirm `API_BASE` matches wherever
  the backend actually is.
- Chat doesn't start → same as above; also check you didn't restart the
  backend (which wipes in-memory sessions) after the frontend already got a
  `session_id`.

# Installing as an App

## Local

```
cd frontend
python -m http.server 8080
```

Visit

http://localhost:8080

Chrome will automatically show the install option.

---

## Android

1. Open the website.

2. Tap

Add to Home Screen

3. Install

---

## Offline

The application uses

- manifest.json

- sw.js

to cache frontend assets.

Once loaded once, the UI works offline.

---

## Play Store

**Route 1 — PWABuilder (no local Android setup needed):**
Deploy website → [pwabuilder.com](https://www.pwabuilder.com) → paste
URL → generate Android App Bundle (.aab) → upload to Google Play
Console.

**Route 2 — Capacitor (more control):** `frontend/capacitor.config.json`
and `frontend/package.json` are already set up — appId
`com.cyberkavach.app`, appName `Cyber Kavach`. From `frontend/`:

```bash
npm install
npx cap add android
npx cap copy
npx cap open android
```

This opens Android Studio → **Build → Generate Signed Bundle / APK** →
follow the signing wizard → upload the resulting `.aab` to Google Play
Console.