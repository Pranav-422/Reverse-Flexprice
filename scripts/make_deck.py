#!/usr/bin/env python3
"""Generate deck.pdf -- exactly 5 slides -- at the repository root.

Dark, card-based layout: a green kicker, a serif headline with one green
keyword, and rounded cards whose border colour carries meaning (green = pass
/ the answer, amber = caution / difference, red = the failure or the alert).
Only reportlab's built-in fonts are used so the deck rebuilds on any machine.

Run:  ./venv/bin/python scripts/make_deck.py
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "deck.pdf"

PAGE = landscape((10 * inch, 5.625 * inch))   # 16:9
W, H = PAGE
M = 0.42 * inch                               # outer margin

C = colors.HexColor
BG, BG_GLOW = C("#06090C"), C("#0C2A19")
CARD, CARD_2 = C("#0C1219"), C("#101823")
LINE = C("#243041")
WHITE, TEXT, MUTED = C("#F3F5F8"), C("#C9D1DC"), C("#8792A2")
GREEN, AMBER, RED, BLUE = C("#22C55E"), C("#F59E0B"), C("#EF4444"), C("#7DB3FF")

SERIF, SANS, BOLD, MONO, MONO_B = "Times-Bold", "Helvetica", "Helvetica-Bold", "Courier", "Courier-Bold"


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def wrap(c, text, font, size, width):
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


def para(c, x, y, text, width, font=SANS, size=8.6, color=MUTED, lead=None):
    """Draw wrapped text from the top-left; returns the y below it."""
    lead = lead or size * 1.38
    c.setFillColor(color)
    c.setFont(font, size)
    for ln in wrap(c, text, font, size, width):
        c.drawString(x, y, ln)
        y -= lead
    return y


def runs(c, x, y, parts, size=8.6):
    """One line of (text, font, colour) runs."""
    for text, font, color in parts:
        c.setFont(font, size)
        c.setFillColor(color)
        c.drawString(x, y, text)
        x += c.stringWidth(text, font, size)
    return x


def card(c, x, y, w, h, border=LINE, fill=CARD, lw=0.9, r=7):
    """Rounded card; (x, y) is the TOP-left corner."""
    c.setFillColor(fill)
    c.setStrokeColor(border)
    c.setLineWidth(lw)
    c.roundRect(x, y - h, w, h, r, stroke=1, fill=1)


def label(c, x, y, text, color=GREEN, size=8.2):
    c.setFillColor(color)
    c.setFont(BOLD, size)
    c.drawString(x, y, text.upper())
    return x + c.stringWidth(text.upper(), BOLD, size)


def chip(c, x, y, text, color, size=7.4):
    """Outlined status chip; (x, y) is the baseline start. Returns the end x."""
    tw = c.stringWidth(text, BOLD, size)
    c.setStrokeColor(color)
    c.setFillColor(CARD_2)
    c.setLineWidth(0.8)
    c.roundRect(x, y - 3.2, tw + 10, size + 6, 3, stroke=1, fill=1)
    c.setFillColor(color)
    c.setFont(BOLD, size)
    c.drawString(x + 5, y, text)
    return x + tw + 16


def connector(c, x1, y1, x2, y2, color=GREEN, dashed=False):
    c.setStrokeColor(color)
    c.setLineWidth(1.1)
    c.setDash(2, 2) if dashed else c.setDash()
    c.line(x1, y1, x2, y2)
    c.setDash()


def chrome(c, index, kicker, title, green_word=None, subtitle=None):
    """Background glow, kicker, serif headline (one word in green), footer."""
    c.setFillColor(BG)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    c.radialGradient(W * 0.92, H * 1.05, W * 0.55, (BG_GLOW, BG), extend=False)
    c.radialGradient(W * 0.02, -H * 0.1, W * 0.45, (C("#0A2015"), BG), extend=False)

    label(c, M, H - 0.42 * inch, kicker, GREEN, 8.6)
    c.setFillColor(MUTED)
    c.setFont(SANS, 7.6)
    c.drawRightString(W - M, H - 0.42 * inch, "Paise-Perfect  |  Team Encode  |  DBG-136")

    size, y = 25, H - 0.80 * inch
    x = M
    for word in title.split(" "):
        c.setFont(SERIF, size)
        c.setFillColor(GREEN if green_word and word.strip(".,") == green_word else WHITE)
        c.drawString(x, y, word)
        x += c.stringWidth(word + " ", SERIF, size)
    if subtitle:
        c.setFillColor(MUTED)
        c.setFont(SANS, 9.4)
        c.drawString(M, y - 17, subtitle)

    c.setFillColor(MUTED)
    c.setFont(SANS, 6.8)
    c.drawRightString(W - M, 0.2 * inch, f"{index} / 5")
    return y - (34 if subtitle else 22)


def banner(c, y, left, right, color=GREEN):
    h = 0.42 * inch
    card(c, M, y, W - 2 * M, h, border=color, lw=1.2)
    c.setFont(BOLD, 11.5)
    lw = c.stringWidth(left + "   ", BOLD, 11.5)
    rw = c.stringWidth(right, BOLD, 11.5)
    x = (W - lw - rw) / 2
    c.setFillColor(WHITE)
    c.drawString(x, y - h / 2 - 4, left)
    c.setFillColor(color)
    c.drawString(x + lw, y - h / 2 - 4, right)


# --------------------------------------------------------------------------- #
# slide 1 -- problem
# --------------------------------------------------------------------------- #
def slide_1(c):
    top = chrome(c, 1, "Problem statement", "The bill says how much. It cannot prove it is right.", "right.",
                 "Indian AI API startups bill in rupees for millions of token events a day. What goes wrong upstream:")
    # left: the event that gets billed twice
    lw, lh = 3.25 * inch, 2.35 * inch
    card(c, M, top, lw, lh)
    c.setStrokeColor(LINE)
    c.setDash(2, 2)
    c.roundRect(M + 8, top - lh + 8, lw - 16, lh - 16, 5, stroke=1, fill=0)
    c.setDash()
    y = top - 24
    c.setFillColor(WHITE)
    c.setFont(BOLD, 10.5)
    c.drawString(M + 18, y, "ONE USAGE EVENT, POSTED TWICE")
    y -= 18
    lines = [
        ([("POST /v1/events", MONO_B, TEXT)]),
        ([('  "event_id": ', MONO, MUTED), ('"evt_retry_10"', MONO_B, GREEN)]),
        ([('  "total_tokens": ', MONO, MUTED), ("50000", MONO_B, WHITE)]),
        ([('  "timestamp": ', MONO, MUTED), ('"12:00:00"', MONO_B, WHITE)]),
        ([("", MONO, MUTED)]),
        ([("network retry ->", MONO, MUTED)]),
        ([('  "timestamp": ', MONO, MUTED), ('"12:00:02"', MONO_B, AMBER)]),
    ]
    for parts in lines:
        runs(c, M + 18, y, parts, 8.2)
        y -= 12.5
    para(c, M + 18, y - 4, "Upstream keys its event store on the client timestamp, and its Redis dedup lock "
         "expires after 24 hours. A retry with a new timestamp is stored as a second row.", lw - 36, size=7.8)

    # middle statement
    mx = M + lw + 0.18 * inch
    mw = 1.55 * inch
    # small fork icon: one line in, two out
    cx, cy = mx + mw / 2, top - lh / 2 + 24
    c.setStrokeColor(GREEN)
    c.setLineWidth(1.6)
    c.line(cx, cy - 7, cx, cy)
    c.line(cx, cy, cx - 6, cy + 7)
    c.line(cx, cy, cx + 6, cy + 7)
    c.setFillColor(WHITE)
    c.setFont(BOLD, 12.5)
    c.drawCentredString(mx + mw / 2, top - lh / 2 + 2, "Same event.")
    c.setFillColor(RED)
    c.drawCentredString(mx + mw / 2, top - lh / 2 - 15, "Billed twice.")
    connector(c, M + lw, top - lh / 2, mx + 6, top - lh / 2, GREEN, dashed=True)

    # right: outcome cards
    rx = mx + mw + 0.12 * inch
    rw = W - M - rx
    ch = 1.05 * inch
    for i, (tag, col, head, body) in enumerate([
        ("RETRY  ·  DOUBLE-BILLED", RED, "2x billed for one request",
         "timestamp sits in the ReplacingMergeTree sort key, so FINAL keeps both rows."),
        ("SUB-PAISE RATE  ·  LOST REVENUE", AMBER, "Rs 0.002 per token rounds to 0 paise",
         "INR is hard-coded to 2 decimals; per-token charges round away before the invoice."),
    ]):
        y0 = top - i * (ch + 0.25 * inch)
        card(c, rx, y0, rw, ch, border=col, lw=1.3)
        label(c, rx + 14, y0 - 20, tag, col, 7.8)
        c.setFillColor(WHITE)
        c.setFont(BOLD, 11.5)
        c.drawString(rx + 14, y0 - 37, head)
        para(c, rx + 14, y0 - 52, body, rw - 28, size=7.9)

    # bottom: three gap cards
    by = top - lh - 0.2 * inch
    bw = (W - 2 * M - 2 * 0.16 * inch) / 3
    bh = 0.92 * inch
    for i, (head, body) in enumerate([
        ("Mid-month upgrades mis-prorate", "Plan-change v2 cannot change interval; credits fall back to list price when a read fails."),
        ("Invoices explain nothing", "A total with no tokens-to-units, tier or proration breakdown is a support ticket."),
        ("11 verified upstream gaps", "Each with path:line evidence at Flexprice 31421e9 in docs/GAPS.md."),
    ]):
        x = M + i * (bw + 0.16 * inch)
        card(c, x, by, bw, bh)
        c.setFillColor(GREEN)
        c.circle(x + 18, by - 22, 3, stroke=0, fill=1)
        c.setFillColor(WHITE)
        c.setFont(BOLD, 10)
        c.drawString(x + 28, by - 25.5, head)
        para(c, x + 14, by - 42, body, bw - 28, size=7.8)


# --------------------------------------------------------------------------- #
# slide 2 -- solution
# --------------------------------------------------------------------------- #
def slide_2(c):
    top = chrome(c, 2, "Proposed solution", "One event in. One exact bill out.", "exact",
                 "Paise-Perfect: a clean-room billing engine in Python + FastAPI + SQLite, built from docs/ alone.")
    # left input card
    lw, lh = 2.25 * inch, 2.75 * inch
    card(c, M, top, lw, lh)
    y = top - 22
    label(c, M + 14, y, "Customer plan")
    y -= 15
    for ln, col in (("Basic  Rs 60 / month", TEXT), ("Growth Rs 120 / month", TEXT), ("Tokens: slab tiers", TEXT)):
        c.setFillColor(col)
        c.setFont(SANS, 8.6)
        c.drawString(M + 18, y, "•  " + ln)
        y -= 12.5
    y -= 8
    label(c, M + 14, y, "Usage event")
    y -= 15
    for parts in ([("event_id ", MONO, MUTED), ("evt_retry_10", MONO_B, GREEN)],
                  [("tokens   ", MONO, MUTED), ("1,500,000", MONO_B, WHITE)],
                  [("upgrade  ", MONO, MUTED), ("day 15", MONO_B, AMBER)]):
        runs(c, M + 18, y, parts, 8.2)
        y -= 12.5
    para(c, M + 14, y - 8, "Money is integer paise; rates are integer micro-paise (1 paise = 10,000). No float ever touches money.",
         lw - 28, size=7.6)

    # flow boxes
    fx = M + lw + 0.32 * inch
    fw, fh, gap = 1.12 * inch, 0.62 * inch, 0.14 * inch
    row_y = top - 0.55 * inch
    boxes = [("INGEST", "UNIQUE event_id"), ("METER", "SUM / 1,000 = units")]
    for i, (h1, h2) in enumerate(boxes):
        x = fx + i * (fw + gap)
        card(c, x, row_y, fw, fh, border=GREEN, lw=1.1)
        c.setFillColor(GREEN)
        c.setFont(BOLD, 9.5)
        c.drawCentredString(x + fw / 2, row_y - 22, h1)
        c.setFillColor(MUTED)
        c.setFont(SANS, 7.2)
        c.drawCentredString(x + fw / 2, row_y - 36, h2)
    connector(c, M + lw, row_y - fh / 2, fx, row_y - fh / 2, GREEN, dashed=True)
    connector(c, fx + fw, row_y - fh / 2, fx + fw + gap, row_y - fh / 2)
    big_y = row_y - fh - 0.3 * inch
    bw2, bh2 = 2 * fw + gap, 1.25 * inch
    connector(c, fx + fw + gap + fw / 2, row_y - fh, fx + fw + gap + fw / 2, big_y)
    card(c, fx, big_y, bw2, bh2, border=GREEN, lw=1.1, fill=CARD_2)
    label(c, fx + 14, big_y - 20, "Price  +  Prorate")
    c.setFillColor(WHITE)
    c.setFont(BOLD, 10)
    c.drawString(fx + 14, big_y - 36, "Deterministic rating engine")
    para(c, fx + 14, big_y - 51, "Slab or volume tiers with inclusive bounds, rated in micro-paise and "
         "rounded half-up once. Day-based proration: 15/30 = 0.5000.", bw2 - 28, size=7.7)

    # right outcome cards
    rx = fx + bw2 + 0.32 * inch
    rw = W - M - rx
    specs = [
        (GREEN, "Bill  ·  integer paise", "Rs 260.00 sealed invoice",
         "Rs 120 plan + Rs 105 tier 1 + Rs 35 tier 2. Idempotent per period."),
        (AMBER, "Explain  ·  \"Why?\"", "\"1,500,000 tokens = 1,500 units...\"",
         "Plain-language bill from local facts; AI only rewords it."),
        (RED, "Alert  ·  runaway agent", "Spike detected: 10x normal",
         "Last hour vs 7-day hourly average, flagged above 3x."),
    ]
    ch, cg = 0.86 * inch, 0.1 * inch
    for i, (col, tag, head, body) in enumerate(specs):
        y0 = top - i * (ch + cg)
        card(c, rx, y0, rw, ch, border=col, lw=1.2)
        label(c, rx + 12, y0 - 17, tag, col, 7.6)
        c.setFillColor(WHITE)
        c.setFont(BOLD, 9.6)
        c.drawString(rx + 12, y0 - 32, head)
        para(c, rx + 12, y0 - 45, body, rw - 24, size=7.5)

    banner(c, top - lh - 0.18 * inch, "Not just a rebuild.", "Money that is never a float.")


# --------------------------------------------------------------------------- #
# slide 3 -- killer tests
# --------------------------------------------------------------------------- #
def slide_3(c):
    top = chrome(c, 3, "Killer tests", "Three tests, written before the engine.", "before",
                 "Every expected value is quoted from docs/PRD.md section 5. Committed red, then built until green.")
    gap = 0.16 * inch
    cw = (W - 2 * M - 2 * gap) / 3
    chh = 2.12 * inch
    tests = [
        ("Killer test 1", "Duplicate event counts once", [
            ("50,000 tokens / 1,000", "50 units"),
            ("retry, changed timestamp", "200 skipped"),
            ("8 concurrent threads", "1 row"),
            ("invoice line quantity", "50, not 100"),
        ]),
        ("Killer test 2", "Day-15 upgrade prorates exactly", [
            ("30-day April cycle", "upgrade Apr 16"),
            ("coefficient", "15/30 = 0.5000"),
            ("unused Starter credit", "-150,000 p"),
            ("remaining Pro charge", "+450,000 p"),
            ("net amount due", "300,000 p"),
        ]),
        ("Killer test 3", "Tiered pricing to the paisa", [
            ("1,500,000 tokens", "1,500 units"),
            ("slab tier 1: 1,000 x 10p + Rs 5", "10,500 p"),
            ("slab tier 2: 500 x 5p + Rs 10", "3,500 p"),
            ("slab total", "Rs 140.00"),
            ("volume: 1,500 x 5p + Rs 10", "Rs 85.00"),
        ]),
    ]
    for i, (tag, head, rows) in enumerate(tests):
        x = M + i * (cw + gap)
        card(c, x, top, cw, chh, border=GREEN, lw=1.2)
        label(c, x + 14, top - 20, tag)
        chip(c, x + cw - 50, top - 20, "PASS", GREEN)
        c.setFillColor(WHITE)
        c.setFont(BOLD, 11)
        y = top - 38
        for ln in wrap(c, head, BOLD, 11, cw - 28):
            c.drawString(x + 14, y, ln)
            y -= 14
        y -= 6
        for k, v in rows:
            c.setStrokeColor(LINE)
            c.setLineWidth(0.6)
            c.line(x + 14, y + 9, x + cw - 14, y + 9)
            c.setFillColor(MUTED)
            c.setFont(SANS, 7.7)
            c.drawString(x + 14, y - 1, k)
            c.setFillColor(WHITE)
            c.setFont(MONO_B, 8)
            c.drawRightString(x + cw - 14, y - 1, v)
            y -= 17
    by = top - chh - 0.2 * inch
    banner(c, by, "pytest: 44 passed", "16 Killer  ·  10 Fix  ·  14 Differentiator  ·  4 Dashboard")
    c.setFillColor(MUTED)
    c.setFont(SANS, 7.4)
    c.drawCentredString(W / 2, by - 0.42 * inch - 14,
                        "Edge cases pinned too: exact tier boundary stays in Tier 1; Day-1 and Day-30 upgrades; aggregate-then-tier.")


# --------------------------------------------------------------------------- #
# slide 4 -- improvements
# --------------------------------------------------------------------------- #
def slide_4(c):
    top = chrome(c, 4, "Innovation & two improvements", "Rules compute. AI only explains.", "only")
    lw = 5.55 * inch
    gap = 0.14 * inch
    # fix card
    fh = 1.42 * inch
    card(c, M, top, lw, fh, border=GREEN, lw=1.3, fill=CARD_2)
    chip(c, M + lw - 128, top - 18, "CLOSES GAPS 1, 7, 11", GREEN, 6.8)
    label(c, M + 14, top - 20, "Fix  ·  Improvement 1", GREEN)
    c.setFillColor(WHITE)
    c.setFont(BOLD, 13)
    c.drawString(M + 14, top - 40, "Deterministic dedup + sub-paise rating")
    y = para(c, M + 14, top - 56, "UNIQUE(tenant_id, event_id) in SQLite: a retry with a new timestamp, or a batch "
             "replayed days later, still dedupes. No cache TTL to outlive.", lw - 28, size=8, color=TEXT)
    para(c, M + 14, y - 2, "Rated in micro-paise, rounded once: 1,500,500 tokens bill 14,003 paise, not 14,005; "
         "Rs 0.002/token over 10 requests bills 2 paise, not 0.", lw - 28, size=8, color=TEXT)

    # two mid cards
    my = top - fh - gap
    mh = 1.12 * inch
    hw = (lw - gap) / 2
    card(c, M, my, hw, mh)
    label(c, M + 12, my - 18, "Ingest outcomes")
    x = M + 12
    yy = my - 38
    x = chip(c, x, yy, "201 CREATED", GREEN)
    chip(c, x, yy, "200 DUPLICATE", MUTED)
    x = chip(c, M + 12, yy - 20, "409 SEALED PERIOD", AMBER)
    chip(c, x, yy - 20, "400 NOT WHOLE", RED)
    para(c, M + 12, yy - 38, "A late event can never land in a finalized invoice.", hw - 24, size=7.4)

    card(c, M + hw + gap, my, hw, mh, border=GREEN, lw=1.1)
    label(c, M + hw + gap + 12, my - 18, "Differentiator  ·  Improvement 2", GREEN, 7.6)
    c.setFillColor(WHITE)
    c.setFont(BOLD, 9.6)
    c.drawString(M + hw + gap + 12, my - 33, "Invoice explainer + spike alert")
    para(c, M + hw + gap + 12, my - 47, "GET /v1/invoices/{id}/explanation  ·  GET /v1/customers/{id}/spike-status. "
         "Closes gaps 8 and 10.", hw - 24, size=7.4)

    # quote card
    qy = my - mh - gap
    qh = top - (qy - 0) - 0  # placeholder
    qh = qy - (0.42 * inch)
    card(c, M, qy, lw, qh)
    label(c, M + 14, qy - 20, "\"Explain my bill\"   ", GREEN)
    c.setFillColor(MUTED)
    c.setFont(BOLD, 7.2)
    c.drawString(M + 140, qy - 20, "LIVE IN /app")
    runs(c, M + 14, qy - 38, [("Customer: ", BOLD, BLUE), ("\"Why is my bill Rs 260?\"", SANS, TEXT)], 8.6)
    y = para(c, M + 14, qy - 54, "Engine: \"You used 1,500,000 tokens (1,500 units). Tier 1: 1,000 units at "
             "10 paise = Rs 105. Tier 2: 500 units at 5 paise = Rs 35. Plus the Rs 120 plan fee.\"",
             lw - 28, size=8.4, color=GREEN)
    para(c, M + 14, y - 4, "Every figure is computed locally. No AI key or an AI failure: the template still answers, HTTP 200.",
         lw - 28, size=7.3)

    # right: why different
    rx = M + lw + 0.18 * inch
    rw = W - M - rx
    rh = top - 0.42 * inch
    card(c, rx, top, rw, rh, border=GREEN, lw=1.3)
    c.setFillColor(GREEN)
    c.setFont(BOLD, 12)
    c.drawString(rx + 14, top - 24, "WHY OURS IS DIFFERENT")
    c.setFillColor(MUTED)
    c.setFont(SANS, 8)
    c.drawString(rx + 14, top - 37, "Versus the upstream engine")
    y = top - 56
    for n, (head, body) in enumerate([
        ("Storage-level dedup", "A database constraint, not a 24h cache."),
        ("Integer money", "Paise and micro-paise; one rounding step."),
        ("Injectable clock", "BILLING_NOW tests a month in milliseconds."),
        ("Sealed periods stay sealed", "409 for late events into a final invoice."),
        ("Explainable bills", "Plain words, never invented numbers."),
        ("Runaway-agent alert", "Flags a prompt loop before the invoice."),
    ], 1):
        c.setFillColor(WHITE)
        c.setFont(BOLD, 9)
        c.drawString(rx + 14, y, f"{n}. {head}")
        y = para(c, rx + 14, y - 11, body, rw - 28, size=7.6) - 5


# --------------------------------------------------------------------------- #
# slide 5 -- demo & results
# --------------------------------------------------------------------------- #
def slide_5(c):
    top = chrome(c, 5, "Demo & results", "Run it locally. Or open it live.", "live.")
    lw = 4.7 * inch
    gap = 0.14 * inch
    # stat cards
    sw = (lw - 3 * gap) / 4
    sh = 0.78 * inch
    for i, (big, lab, sub, col) in enumerate([
        ("44", "pytest cases", "all passing", GREEN), ("67", "demo checks", "every one PASS", GREEN),
        ("~2s", "suite runtime", "BILLING_NOW", WHITE), ("0", "external services", "SQLite only", WHITE),
    ]):
        x = M + i * (sw + gap)
        card(c, x, top, sw, sh)
        c.setFillColor(col)
        c.setFont(BOLD, 20)
        c.drawString(x + 10, top - 28, big)
        c.setFillColor(WHITE)
        c.setFont(BOLD, 7.6)
        c.drawString(x + 10, top - 42, lab)
        c.setFillColor(MUTED)
        c.setFont(SANS, 6.9)
        c.drawString(x + 10, top - 53, sub)
    # screenshot card
    iy = top - sh - gap
    ih = iy - 0.95 * inch
    card(c, M, iy, lw, ih, border=GREEN, lw=1.1)
    label(c, M + 12, iy - 17, "Live dashboard  ·  /app", GREEN, 7.6)
    shot = ROOT / "assets" / "deck_dashboard.png"
    if shot.exists():
        img = ImageReader(str(shot))
        iw0, ih0 = img.getSize()
        box_w, box_h = lw - 24, ih - 30
        s = min(box_w / iw0, box_h / ih0)
        dw, dh = iw0 * s, ih0 * s
        c.drawImage(img, M + 12 + (box_w - dw) / 2, iy - 24 - dh, width=dw, height=dh, mask="auto")

    # right column: run + verified
    rx = M + lw + 0.18 * inch
    rw = W - M - rx
    rh1 = 1.45 * inch
    card(c, rx, top, rw, rh1, fill=C("#05080B"))
    label(c, rx + 12, top - 17, "Run it", GREEN, 7.6)
    y = top - 33
    for ln, col in (("pip install -r requirements.txt", TEXT), ("cp .env.example .env", TEXT),
                    ("pytest -q            # 44 passed", GREEN), ("./scripts/demo       # 67 PASS", GREEN),
                    ("./scripts/ui         # /app dashboard", GREEN), ("Windows: scripts\\demo, scripts\\ui", MUTED)):
        c.setFillColor(col)
        c.setFont(MONO_B if col is GREEN else MONO, 7.6)
        c.drawString(rx + 12, y, ln)
        y -= 11.5
    vy = top - rh1 - gap
    vh = vy - 0.95 * inch
    card(c, rx, vy, rw, vh, border=GREEN, lw=1.1)
    label(c, rx + 12, vy - 17, "Verified live", GREEN, 7.6)
    y = vy - 33
    for item in ("Duplicate + 8-way concurrent events count once",
                 "Day-15 upgrade nets exactly Rs 3,000.00",
                 "Slab Rs 140 / Volume Rs 85, to the paisa",
                 "Rs 0.002/token bills 2 paise, not 0",
                 "Explanation returns 200 with no AI key",
                 "Runaway loop flagged at 10x baseline"):
        c.setFillColor(GREEN)
        c.setFont(BOLD, 8)
        c.drawString(rx + 12, y, "✓")
        y = para(c, rx + 24, y, item, rw - 36, size=7.6, color=TEXT) - 3

    banner(c, 0.42 * inch + 0.42 * inch + 4, "Live preview:", "paise-perfect.vercel.app   ·   github.com/Pranav-422/Reverse-Flexprice")


def main():
    c = canvas.Canvas(str(OUT), pagesize=PAGE)
    c.setTitle("Paise-Perfect - Team Encode (DBG-136)")
    c.setAuthor("Team Encode")
    c.setSubject("Usage-Based Billing Engine - clean-room rebuild from docs/")
    for fn in (slide_1, slide_2, slide_3, slide_4, slide_5):
        fn(c)
        c.showPage()
    c.save()
    print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
