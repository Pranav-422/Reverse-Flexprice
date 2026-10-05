"""SQLite DDL, transcribed from DATA_MODEL.md section 3.

All monetary columns are INTEGER paise; all unit rates are INTEGER micro-paise.
Floating-point currency columns are prohibited (DATA_MODEL.md section 1).
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    external_id TEXT NOT NULL,
    name        TEXT NOT NULL,
    email       TEXT,
    timezone    TEXT NOT NULL DEFAULT 'UTC',
    created_at  INTEGER NOT NULL,
    UNIQUE (tenant_id, external_id)
);

CREATE TABLE IF NOT EXISTS meters (
    id               TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    name             TEXT NOT NULL,
    event_name       TEXT NOT NULL,
    aggregation_type TEXT NOT NULL,
    value_property   TEXT,
    divide_by        INTEGER NOT NULL DEFAULT 1,
    round_type       TEXT NOT NULL DEFAULT 'none',
    created_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    name       TEXT NOT NULL,
    lookup_key TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (tenant_id, lookup_key)
);

CREATE TABLE IF NOT EXISTS prices (
    id                 TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    plan_id            TEXT NOT NULL REFERENCES plans(id),
    meter_id           TEXT REFERENCES meters(id),
    type               TEXT NOT NULL,            -- 'fixed' | 'usage'
    tier_mode          TEXT,                     -- 'volume' | 'slab'
    fixed_amount_paise INTEGER NOT NULL DEFAULT 0,
    display_name       TEXT,
    created_at         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS price_tiers (
    id                      TEXT PRIMARY KEY,
    price_id                TEXT NOT NULL REFERENCES prices(id),
    tier_index              INTEGER NOT NULL,
    up_to_units             INTEGER,             -- NULL = final infinite tier
    unit_amount_micro_paise INTEGER NOT NULL,
    flat_amount_paise       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (price_id, tier_index)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL,
    customer_id          TEXT NOT NULL REFERENCES customers(id),
    plan_id              TEXT NOT NULL REFERENCES plans(id),
    status               TEXT NOT NULL,          -- active | cancelled | paused
    billing_anchor       INTEGER NOT NULL,
    current_period_start INTEGER NOT NULL,       -- inclusive
    current_period_end   INTEGER NOT NULL,       -- exclusive
    created_at           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subscription_line_items (
    id              TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
    price_id        TEXT NOT NULL REFERENCES prices(id),
    quantity        INTEGER NOT NULL DEFAULT 1,
    start_date      INTEGER NOT NULL,
    end_date        INTEGER
);

-- DEDUPLICATION CORE (DATA_MODEL.md section 6).
CREATE TABLE IF NOT EXISTS usage_events (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    event_id        TEXT NOT NULL,               -- client idempotency key
    customer_id     TEXT NOT NULL REFERENCES customers(id),
    event_name      TEXT NOT NULL,
    timestamp       INTEGER NOT NULL,
    quantity        INTEGER NOT NULL,
    properties_json TEXT,
    ingested_at     INTEGER NOT NULL
);

-- THE constraint the entire dedup guarantee rests on. Any retry, duplicate
-- post or replayed batch with the same event_id is rejected at the index
-- layer, regardless of how the client timestamp changed or how long ago the
-- original arrived (fixes GAPS.md gaps 1 and 11).
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_events_tenant_event_unique
    ON usage_events (tenant_id, event_id);

CREATE INDEX IF NOT EXISTS idx_usage_events_lookup
    ON usage_events (tenant_id, customer_id, event_name, timestamp);

CREATE TABLE IF NOT EXISTS invoices (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    customer_id     TEXT NOT NULL REFERENCES customers(id),
    subscription_id TEXT REFERENCES subscriptions(id),
    idempotency_key TEXT NOT NULL,
    invoice_type    TEXT NOT NULL,               -- subscription_cycle | one_off
    status          TEXT NOT NULL,               -- draft | finalized | voided
    period_start    INTEGER NOT NULL,
    period_end      INTEGER NOT NULL,
    subtotal_paise  INTEGER NOT NULL,
    total_paise     INTEGER NOT NULL,
    amount_due_paise INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS invoice_line_items (
    id                TEXT PRIMARY KEY,
    invoice_id        TEXT NOT NULL REFERENCES invoices(id),
    price_id          TEXT REFERENCES prices(id),
    description       TEXT NOT NULL,
    quantity          INTEGER NOT NULL,
    unit_amount_paise INTEGER NOT NULL,
    amount_paise      INTEGER NOT NULL,
    metadata_json     TEXT,
    position          INTEGER NOT NULL DEFAULT 0
);
"""

# DATA_MODEL.md section 7 also mandates UNIQUE(subscription_id, period_start,
# period_end). It is a partial index because one_off proration invoices share a
# subscription and an instantaneous period, so only cycle invoices are covered.
CYCLE_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_subscription_period_unique
    ON invoices (subscription_id, period_start, period_end)
    WHERE invoice_type = 'subscription_cycle';
"""
