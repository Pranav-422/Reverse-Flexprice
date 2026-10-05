#!/usr/bin/env python3
"""Generate deck.pdf -- exactly 5 slides -- at the repository root.

Run:  ./venv/bin/python scripts/make_deck.py
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "deck.pdf"

PAGE = landscape((10 * inch, 6.9 * inch))
W, H = PAGE
MARGIN = 0.7 * inch

INK = colors.HexColor("#14213D")
ACCENT = colors.HexColor("#C1440E")
MUTED = colors.HexColor("#5A6478")
RULE = colors.HexColor("#D9DCE3")
GOOD = colors.HexColor("#1B6B3A")
BG = colors.HexColor("#FBFAF7")

SLIDES = [
    {
        "kicker": "01 / PROBLEM",
        "title": "Indian AI API startups are billing wrong",
        "lead": "They charge in rupees for millions of micro-transactions per day "
                "- per 1,000 LLM tokens, per embedding, per inference. Four "
                "failure modes cost them real money:",
        "bullets": [
            ("Retries double-bill.", "A network hiccup makes the gateway re-POST "
             "a usage event. Upstream keys its event store on the client "
             "timestamp, so a retry with a changed timestamp persists twice and "
             "the customer pays twice."),
            ("Mid-month upgrades mis-prorate.", "Day-15 plan changes either fail "
             "outright or produce credits and charges that don't net to the right "
             "number."),
            ("Sub-paise rates round to zero.", "INR precision is hardcoded to 2 "
             "decimals. At Rs 0.002 per token the charge rounds to 0 and the "
             "revenue disappears."),
            ("Invoices explain nothing.", "A number with no breakdown of tokens, "
             "tiers, or proration is a support ticket waiting to happen."),
        ],
        "stats": [
            ("2x", "billed per retried event",
             "a re-POST with a changed timestamp persists as a second row"),
            ("24h", "dedup cache TTL upstream",
             "a batch replayed after it bypasses deduplication entirely"),
            ("0", "paise billed at Rs 0.002/token",
             "sub-paise rates round away before they reach the invoice"),
            ("11", "verified upstream gaps",
             "catalogued with file:line evidence in docs/GAPS.md"),
        ],
        "footer": "Source: docs/PRD.md section 1 and docs/GAPS.md section 1 "
                  "(11 verified upstream gaps).",
    },
    {
        "kicker": "02 / OUR REBUILD",
        "title": "A deterministic billing engine, built from docs/ alone",
        "lead": "Clean-room rebuild in Python 3 + FastAPI + SQLite. Zero external "
                "infrastructure: no Postgres, no ClickHouse, no Redis, no Kafka, "
                "no Temporal.",
        "arch": [
            ("AI Gateway", "POST /v1/events"),
            ("Ingestion", "atomic insert; UNIQUE(tenant_id, event_id) is the "
                          "dedup arbiter"),
            ("Meters", "half-open windows; SUM / COUNT / COUNT_UNIQUE; "
                       "divide_by packaging"),
            ("Pricing", "slab + volume tiers; inclusive up_to; integer "
                        "micro-paise"),
            ("Proration", "day coefficient; opposing credit / debit lines"),
            ("Invoicing", "integer-paise totals; idempotent; + AI explainer"),
        ],
        "bullets": [
            ("Money is never a float.", "Amounts are integer paise (1 INR = 100). "
             "Unit rates are integer micro-paise (1 paise = 10,000). Half-up "
             "rounding happens exactly once, at the invoice line."),
            ("Time is injectable.", "Nothing calls datetime.now(). BILLING_NOW "
             "from .env drives the clock, so a 30-day cycle and a Day-15 upgrade "
             "are simulated in milliseconds - the whole suite runs in ~1 second."),
            ("Durability before acknowledgement.", "HTTP 201 is returned only "
             "after the row is committed. No fire-and-forget broker to lose it."),
        ],
        "footer": "Build order and component split follow docs/ARCHITECTURE.md "
                  "sections 3 and 5.",
    },
    {
        "kicker": "03 / KILLER TESTS",
        "title": "Three tests, written before a line of engine code",
        "lead": "Every expected value below is quoted from docs/PRD.md section 5. "
                "Tests were committed red, then the engine was built until they "
                "passed.",
        "tests": [
            ("KILLER TEST 1", "Duplicate usage event counts once",
             ["50,000 raw tokens / divide_by 1,000  =  50 billable units",
              "Retry with a CHANGED timestamp  ->  HTTP 200 duplicate_skipped",
              "8 concurrent threads on evt_race_99  ->  exactly 1 row written",
              "Invoice line quantity = 50, never 100"]),
            ("KILLER TEST 2", "Mid-month upgrade prorates exactly",
             ["30-day April cycle, upgrade on Day 15 (Apr 16 00:00 UTC)",
              "coefficient = 15 / 30 = 0.5000",
              "Unused Starter credit   =  -150,000 paise  (-Rs 1,500.00)",
              "Remaining Pro charge    =  +450,000 paise  (+Rs 4,500.00)",
              "NET AMOUNT DUE          =   300,000 paise  ( Rs 3,000.00)"]),
            ("KILLER TEST 3", "Tiered pricing matches the hand calculation",
             ["1,500,000 tokens = 1,500 units against a 2-tier table",
              "SLAB:  1,000 x Rs 0.10 + Rs 5.00   =  10,500 paise",
              "     +   500 x Rs 0.05 + Rs 10.00  =   3,500 paise",
              "     =                                14,000 paise = Rs 140.00",
              "VOLUME: 1,500 x Rs 0.05 + Rs 10.00 =   8,500 paise = Rs  85.00"]),
        ],
        "status": "pytest: 35 passed  -  16 Killer Test cases, 7 Fix, 12 "
                  "Differentiator",
        "footer": "Edge cases pinned too: exact tier boundary stays in Tier 1; "
                  "Day-1 and Day-30 upgrades; aggregate-then-tier.",
    },
    {
        "kicker": "04 / IMPROVEMENTS",
        "title": "One fix, one differentiator - both specified in docs/GAPS.md",
        "two_col": [
            {
                "tag": "FIX",
                "head": "Deterministic dedup + sub-paise rating",
                "sub": "docs/GAPS.md Improvement 1  -  closes gaps 1, 7, 11",
                "rows": [
                    ("Dedup at the storage layer",
                     "UNIQUE(tenant_id, event_id) in SQLite. A retry with a "
                     "mutated timestamp, or a batch replayed 7 days later, still "
                     "dedupes - there is no cache TTL to outlive."),
                    ("Rate in micro-paise, round once",
                     "Rs 0.002/token = 2,000 micro-paise. Ten single-token "
                     "requests bill 2 paise, not 0."),
                    ("The defect the tests caught",
                     "Billable units were rounded through divide_by BEFORE any "
                     "money was computed. 1,500,500 tokens billed 14,005 paise "
                     "instead of the true 14,002.5 -> 14,003. Rating now stays "
                     "in raw-token space over an exact integer numerator."),
                    ("Acceptance criterion",
                     "Concurrent evt_retry_10 -> 1 row, 50,000 tokens, exactly "
                     "500 paise (Rs 5.00). Zero duplicate leakage."),
                ],
            },
            {
                "tag": "DIFFERENTIATOR",
                "head": "Invoice explainer + runaway-spike alert",
                "sub": "docs/GAPS.md Improvement 2  -  closes gaps 8, 10",
                "rows": [
                    ("GET /v1/invoices/{id}/explanation",
                     "One paragraph explaining how raw tokens became billable "
                     "units, which tiers applied, and the exact proration credit "
                     "and charge."),
                    ("GET /v1/customers/{id}/spike-status",
                     "Trailing-hour velocity against the 7-day hourly average. "
                     "Over SPIKE_THRESHOLD_FACTOR (3.0x) it flags and logs - "
                     "catching the agent stuck in a prompt loop."),
                    ("AI is optional and cannot break the app",
                     "Key from .env. Missing key, missing package, bad key, API "
                     "error, or a refusal all return HTTP 200 from the "
                     "deterministic template, with source and ai_error on the "
                     "response so the fallback is visible, not silent."),
                    ("The model never does arithmetic",
                     "Every figure is computed locally from the sealed invoice; "
                     "claude-opus-5 only rewrites those facts. An AI outage "
                     "changes the wording, never the money."),
                ],
            },
        ],
        "footer": "No other gaps were touched. Full pytest run after each "
                  "improvement; the Killer Tests never broke.",
    },
    {
        "kicker": "05 / DEMO & RESULTS",
        "title": "Everything runs from two commands",
        "code": [
            "python3 -m venv venv && ./venv/bin/pip install -r requirements.txt",
            "cp .env.example .env",
            "",
            "./venv/bin/python -m pytest -q      # 35 passed in ~1s",
            "./scripts/demo                      # 67 live checks, all PASS",
        ],
        "lead": "scripts/demo boots a real uvicorn server on a throwaway SQLite "
                "file and drives it over HTTP, printing every request with the "
                "EXPECTED value quoted from docs/PRD.md beside the ACTUAL "
                "response.",
        "results": [
            ("35", "pytest cases passing", "16 Killer / 7 Fix / 12 Differentiator"),
            ("67", "live demo checks", "every one PASS"),
            ("~1s", "full suite runtime", "BILLING_NOW, no sleeps"),
            ("0", "external services", "SQLite only"),
        ],
        "verified": [
            "Duplicate and 8-way concurrent events count exactly once",
            "Day-15 upgrade nets exactly 300,000 paise (Rs 3,000.00)",
            "Slab 14,000 paise / Volume 8,500 paise, to the paise",
            "Rs 0.002-per-token usage bills 2 paise instead of rounding to 0",
            "Invoice explanation returns 200 with no AI key configured",
            "Runaway loop flagged at 10.00x the 7-day baseline",
        ],
        "footer": "No secrets in git: .env, venv/, node_modules/ and *.db are "
                  "gitignored; .env.example carries names only.",
    },
]


# --------------------------------------------------------------------------- #
def wrap(c, text, font, size, width):
    c.setFont(font, size)
    words, line, out = text.split(), "", []
    for w in words:
        trial = f"{line} {w}".strip()
        if c.stringWidth(trial, font, size) > width and line:
            out.append(line)
            line = w
        else:
            line = trial
    if line:
        out.append(line)
    return out


def chrome(c, slide, index):
    c.setFillColor(BG)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    c.setFillColor(ACCENT)
    c.rect(0, H - 0.16 * inch, W, 0.16 * inch, stroke=0, fill=1)

    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(MARGIN, H - 0.62 * inch, slide["kicker"])

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8.5)
    c.drawRightString(W - MARGIN, H - 0.62 * inch,
                      "Core Billing Engine  |  Team Encode  |  DBG-136")

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(MARGIN, H - 1.05 * inch, slide["title"])

    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.line(MARGIN, H - 1.22 * inch, W - MARGIN, H - 1.22 * inch)

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawString(MARGIN, 0.42 * inch, slide["footer"])
    c.drawRightString(W - MARGIN, 0.42 * inch, f"{index} / 5")
    return H - 1.52 * inch


def draw_lead(c, slide, y, width=None):
    if "lead" not in slide:
        return y
    width = width or (W - 2 * MARGIN)
    c.setFillColor(INK)
    for ln in wrap(c, slide["lead"], "Helvetica", 10.5, width):
        c.setFont("Helvetica", 10.5)
        c.drawString(MARGIN, y, ln)
        y -= 14
    return y - 8


def draw_bullets(c, bullets, y, width, x=MARGIN):
    for head, body in bullets:
        c.setFillColor(ACCENT)
        c.circle(x + 3, y + 3.2, 2.6, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10)
        hw = c.stringWidth(head, "Helvetica-Bold", 10)
        c.drawString(x + 12, y, head)
        first = True
        avail = width - 12
        c.setFillColor(MUTED)
        for ln in wrap(c, body, "Helvetica", 9.5, avail - (hw + 4 if first else 0)):
            c.setFont("Helvetica", 9.5)
            if first:
                c.drawString(x + 12 + hw + 4, y, ln)
                first = False
            else:
                y -= 12.5
                c.drawString(x + 12, y, ln)
        y -= 20
    return y


# --------------------------------------------------------------------------- #
def draw_stat_band(c, stats, y):
    """Four impact figures across the width."""
    card_w = (W - 2 * MARGIN - 0.45 * inch) / 4
    for n, (big, label, sub) in enumerate(stats):
        x = MARGIN + n * (card_w + 0.15 * inch)
        c.setFillColor(colors.white)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.8)
        c.rect(x, y - 1.0 * inch, card_w, 1.0 * inch, stroke=1, fill=1)
        c.setFillColor(ACCENT)
        c.rect(x, y - 0.035 * inch, card_w, 0.035 * inch, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 26)
        c.drawString(x + 11, y - 0.4 * inch, big)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 8.8)
        c.drawString(x + 11, y - 0.58 * inch, label)
        c.setFillColor(MUTED)
        yy = y - 0.72 * inch
        for ln in wrap(c, sub, "Helvetica", 7.6, card_w - 22):
            c.setFont("Helvetica", 7.6)
            c.drawString(x + 11, yy, ln)
            yy -= 9.5
    return y - 1.0 * inch


def slide_1(c, s, i):
    y = draw_lead(c, s, chrome(c, s, i))
    y = draw_bullets(c, s["bullets"], y, W - 2 * MARGIN)
    y -= 6
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(MARGIN, y, "What it costs them")
    draw_stat_band(c, s["stats"], y - 14)


def slide_2(c, s, i):
    y = draw_lead(c, s, chrome(c, s, i))
    box_h = 0.33 * inch
    gap = 9
    for n, (name, desc) in enumerate(s["arch"]):
        top = y - n * (box_h + gap)
        c.setFillColor(colors.white)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.8)
        c.rect(MARGIN, top - box_h, W - 2 * MARGIN, box_h, stroke=1, fill=1)
        c.setFillColor(ACCENT)
        c.rect(MARGIN, top - box_h, 2.5, box_h, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(MARGIN + 12, top - box_h / 2 - 3.5, name)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8.8)
        c.drawString(MARGIN + 1.25 * inch, top - box_h / 2 - 3.5, desc)
        if n < len(s["arch"]) - 1:
            # Arrow sits centred inside the gap below this box.
            cx = MARGIN + 0.45 * inch
            c.setStrokeColor(ACCENT)
            c.setLineWidth(1.1)
            c.line(cx, top - box_h - 1, cx, top - box_h - gap + 4)
            c.setFillColor(ACCENT)
            c.setLineWidth(0)
            path = c.beginPath()
            path.moveTo(cx - 3, top - box_h - gap + 5)
            path.lineTo(cx + 3, top - box_h - gap + 5)
            path.lineTo(cx, top - box_h - gap + 1)
            path.close()
            c.drawPath(path, stroke=0, fill=1)
    y -= len(s["arch"]) * (box_h + gap) + 6
    draw_bullets(c, s["bullets"], y, W - 2 * MARGIN)


def _slide_3_card_height(c, s, col_w):
    """Tallest column decides the shared card height, so nothing overflows."""
    tallest = 0
    for _, head, rows in s["tests"]:
        h = 0.52 * inch
        h += 13 * len(wrap(c, head, "Helvetica-Bold", 10, col_w - 16))
        h += 6
        for row in rows:
            h += 10 * len(wrap(c, row, "Courier", 7.3, col_w - 16)) + 3
        tallest = max(tallest, h)
    return tallest + 10


def slide_3(c, s, i):
    y = draw_lead(c, s, chrome(c, s, i))
    col_w = (W - 2 * MARGIN - 0.36 * inch) / 3
    card_h = _slide_3_card_height(c, s, col_w)
    for n, (tag, head, rows) in enumerate(s["tests"]):
        x = MARGIN + n * (col_w + 0.18 * inch)
        c.setFillColor(colors.white)
        c.setStrokeColor(RULE)
        c.rect(x, y - card_h, col_w, card_h, stroke=1, fill=1)
        c.setFillColor(GOOD)
        c.rect(x, y - 0.3 * inch, col_w, 0.3 * inch, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + 8, y - 0.2 * inch, f"{tag}   PASS")
        yy = y - 0.52 * inch
        c.setFillColor(INK)
        for ln in wrap(c, head, "Helvetica-Bold", 10, col_w - 16):
            c.setFont("Helvetica-Bold", 10)
            c.drawString(x + 8, yy, ln)
            yy -= 13
        yy -= 6
        c.setFillColor(MUTED)
        for row in rows:
            for k, ln in enumerate(wrap(c, row, "Courier", 7.3, col_w - 16)):
                c.setFont("Courier", 7.3)
                c.drawString(x + 8 + (0 if k == 0 else 6), yy, ln)
                yy -= 10
            yy -= 3
    y -= card_h + 16
    c.setFillColor(GOOD)
    c.rect(MARGIN, y - 0.3 * inch, W - 2 * MARGIN, 0.3 * inch, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(W / 2, y - 0.2 * inch, s["status"])


def slide_4(c, s, i):
    y = chrome(c, s, i)
    col_w = (W - 2 * MARGIN - 0.3 * inch) / 2
    for n, col in enumerate(s["two_col"]):
        x = MARGIN + n * (col_w + 0.3 * inch)
        c.setFillColor(ACCENT if n == 0 else INK)
        c.rect(x, y - 0.28 * inch, col_w, 0.28 * inch, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + 8, y - 0.19 * inch, col["tag"])
        yy = y - 0.5 * inch
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 12.5)
        for ln in wrap(c, col["head"], "Helvetica-Bold", 12.5, col_w):
            c.drawString(x, yy, ln)
            yy -= 15
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Oblique", 8.5)
        c.drawString(x, yy, col["sub"])
        yy -= 18
        for head, body in col["rows"]:
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 9.3)
            for ln in wrap(c, head, "Helvetica-Bold", 9.3, col_w):
                c.drawString(x, yy, ln)
                yy -= 11.5
            c.setFillColor(MUTED)
            for ln in wrap(c, body, "Helvetica", 8.6, col_w):
                c.setFont("Helvetica", 8.6)
                c.drawString(x, yy, ln)
                yy -= 10.5
            yy -= 8


def slide_5(c, s, i):
    y = chrome(c, s, i)
    code_h = 1.12 * inch
    c.setFillColor(INK)
    c.rect(MARGIN, y - code_h, W - 2 * MARGIN, code_h, stroke=0, fill=1)
    yy = y - 0.2 * inch
    for ln in s["code"]:
        c.setFillColor(colors.HexColor("#8FE3B0") if ln.startswith("./venv/bin/python -m")
                       or ln.startswith("./scripts") else colors.white)
        c.setFont("Courier-Bold", 9)
        c.drawString(MARGIN + 12, yy, ln)
        yy -= 12.5
    y -= code_h + 14

    y = draw_lead(c, s, y)

    card_w = (W - 2 * MARGIN - 0.45 * inch) / 4
    for n, (big, label, sub) in enumerate(s["results"]):
        x = MARGIN + n * (card_w + 0.15 * inch)
        c.setFillColor(colors.white)
        c.setStrokeColor(RULE)
        c.rect(x, y - 0.78 * inch, card_w, 0.78 * inch, stroke=1, fill=1)
        c.setFillColor(GOOD)
        c.setFont("Helvetica-Bold", 22)
        c.drawString(x + 10, y - 0.34 * inch, big)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 8.6)
        c.drawString(x + 10, y - 0.5 * inch, label)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.6)
        c.drawString(x + 10, y - 0.64 * inch, sub)
    y -= 0.95 * inch

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN, y, "Verified live in the demo")
    y -= 15
    half = W / 2 - MARGIN + 0.1 * inch
    for n, item in enumerate(s["verified"]):
        x = MARGIN if n % 2 == 0 else W / 2
        yy = y - (n // 2) * 13.5
        c.setFillColor(GOOD)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x, yy, "+")
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(x + 11, yy, item)


def main():
    c = canvas.Canvas(str(OUT), pagesize=PAGE)
    c.setTitle("Core Billing Engine - Team Encode (DBG-136)")
    c.setAuthor("Team Encode")
    c.setSubject("Usage-Based Billing Engine - clean-room rebuild from docs/")
    for n, (slide, fn) in enumerate(
            zip(SLIDES, [slide_1, slide_2, slide_3, slide_4, slide_5]), start=1):
        fn(c, slide, n)
        c.showPage()
    c.save()
    print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
