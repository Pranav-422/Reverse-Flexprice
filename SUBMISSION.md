# Submission

**Solution title:** Paise-Perfect — usage billing for Indian AI APIs that never double-bills and explains every rupee

**Team ID:** DBG-136

**Team:** Encode

**Card:** Usage-Based Billing Engine

**Original:** https://github.com/flexprice/flexprice
*(The rebuild agent never saw the original; it built only from `docs/`. The upstream
repository was read earlier, by a separate reverse-engineering pass, solely to author
the `docs/` folder — the engine in this repository contains no upstream code and
depends on no upstream package.)*

**Commit studied:** [`31421e9ff62d00a9f4cdded11d0aad5d32a22f4a`](https://github.com/flexprice/flexprice/tree/31421e9ff62d00a9f4cdded11d0aad5d32a22f4a)
(Flexprice `main`, 3 Oct 2026 — `git -C flexprice rev-parse HEAD` on the clone the docs were written from;
every `path:line` in `docs/` refers to this commit)

**Run (macOS / Linux):**

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt && cp .env.example .env && ./venv/bin/python -m pytest -q && ./scripts/demo
```

**Run (Windows, PowerShell or cmd):**

```bat
py -3 -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
venv\Scripts\python -m pytest -q
scripts\demo
```

Individually:

| Step | Command | Expected |
|---|---|---|
| Install | `python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`<br>Windows: `py -3 -m venv venv` then `venv\Scripts\pip install -r requirements.txt` | — |
| Configure | `cp .env.example .env` (Windows: `copy .env.example .env`) | no secrets needed |
| Tests | `./venv/bin/python -m pytest -q` (Windows: `venv\Scripts\python -m pytest -q`) | **40 passed** (~2s) |
| Demo | `./scripts/demo` (Windows: `scripts\demo`) | **67 checks, all PASS** |
| Dashboard | `./scripts/ui` (Windows: `scripts\ui`) | opens http://127.0.0.1:8000/app — all three Killer Tests and both improvements, live |
| Server | `./venv/bin/uvicorn app.main:app --reload` (Windows: `venv\Scripts\uvicorn app.main:app --reload`) | http://127.0.0.1:8000/docs |

**Improvements we built:**

1. **Fix:** Deterministic deduplication with sub-paise micro-token rating
   (`docs/GAPS.md` section 2, Improvement 1 — closes upstream gaps 1, 7 and 11).
   `UNIQUE(tenant_id, event_id)` makes dedup a storage-engine guarantee, so a retry
   with a mutated timestamp or a batch replayed days later still dedupes — there is
   no cache TTL to outlive and no dependence on the client clock. Unit rates are
   stored as integer micro-paise and consumption is rated in raw-token space over an
   exact integer numerator, with half-up rounding applied **once**, at the invoice
   line. This caught a genuine defect: billable units were previously rounded through
   `divide_by` *before* any money was computed, so 1,500,500 tokens billed 14,005
   paise instead of the correct 14,002.5 → 14,003. It also means a ₹0.002-per-token
   rate bills 2 paise across ten single-token requests instead of rounding to zero.
   Acceptance criterion verified: two concurrent posts of `evt_retry_10` → one row,
   50,000 tokens, exactly 500 paise (₹5.00), zero duplicate leakage.

2. **Differentiator:** Plain-language invoice explainer + runaway usage-spike alert
   (`docs/GAPS.md` section 2, Improvement 2 — closes upstream gaps 8 and 10).
   `GET /v1/invoices/{id}/explanation` returns a paragraph explaining how raw tokens
   became billable units, which tiers applied, and the exact proration credit and
   charge. `GET /v1/customers/{id}/spike-status` compares trailing-hour token
   velocity against the 7-day hourly average and flags (and logs) anything above
   `SPIKE_THRESHOLD_FACTOR` — catching an autonomous agent stuck in a prompt loop.
   **The AI is optional and cannot break the app:** every figure is computed locally
   from the sealed invoice and the model only rewrites those facts, so an AI failure
   changes the wording and never the money. A missing key, a missing package, a bad
   key, an API error or a model refusal all return `200 OK` from the deterministic
   `docs/GAPS.md` template, with `source` and `ai_error` on the response so the
   fallback is visible rather than silent. Both the missing-SDK and API-error paths
   are covered by tests. **On screen:** `./scripts/ui` (Windows: `scripts\ui`) seeds
   a demo tenant and opens the `/app` dashboard — the bill in plain words with a
   line-by-line breakdown, and a red "Spike detected · 10× normal" banner next to
   a calm customer. The same dashboard runs all three Killer Tests live
   (concurrent duplicate post, Day-15 upgrade, slab vs volume slider).

**Libraries / AI used:**

* **FastAPI** — HTTP routing, request validation and the OpenAPI surface for the
  `/v1` API specified in `docs/API.md`.
* **Uvicorn** — ASGI server; the demo boots a real one so the walkthrough exercises
  genuine HTTP rather than an in-process shim.
* **Pydantic** — comes with FastAPI; used for typed settings and response shaping.
* **SQLite (Python standard library `sqlite3`)** — the entire datastore.
  `docs/ARCHITECTURE.md` section 4 chose it to eliminate external infrastructure, and
  its `UNIQUE` index is what makes the deduplication guarantee real. WAL mode plus a
  busy timeout lets concurrent ingestion threads contend on the index rather than
  erroring out.
* **python-dotenv** — loads `.env` so time windows and the optional AI key come from
  the environment, never from the code.
* **httpx** — HTTP client for `scripts/demo` and the test client.
* **pytest** — the test runner for all 40 cases.
* **reportlab** — generates `deck.pdf` from `scripts/make_deck.py`, so the deck is
  reproducible from source rather than hand-built.
* **anthropic** (**optional**, deliberately left commented out in
  `requirements.txt`) — the SDK for the invoice explainer. Leaving it uninstalled by
  default means the standard install and the full test suite exercise the
  deterministic fallback path.
* **Google Stitch** (design tool, not a runtime dependency) — generated the visual
  mock-ups the `/app` dashboard's layout follows. The page itself was written by hand
  as plain HTML/CSS/JS with no framework or build step, and every figure on it is
  fetched from the engine's API; Stitch's placeholder content was not used.

**AI model:** **Claude Opus 5.5** (`claude-opus-5-5`), via the Anthropic Messages
API, used only for the plain-language invoice explainer. *Why:* the task is pure
natural-language rewriting of pre-computed billing facts into something a customer
will not open a support ticket about, and Opus 5.5 follows a tight "use only the
numbers given, invent nothing" instruction reliably. `docs/ARCHITECTURE.md` section 2
lists Anthropic as a supported provider. It is called at `low` effort (verified
supported on this model) with a 20-second timeout and a single retry; no `thinking`
parameter is sent, because adaptive thinking is always on for Opus 5.5 and
`thinking: {"type": "disabled"}` returns a 400 there. The key lives in `.env`
(`AI_PROVIDER_API_KEY`), is never committed, and the engine runs identically
without it.

**Deck:** `deck.pdf` (repo root) — 5 slides, generated by
`./venv/bin/python scripts/make_deck.py`.
