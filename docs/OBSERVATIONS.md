# System Observations & Reverse-Engineered Findings: Flexprice Core

This document catalogs every verified architectural claim, code path, and core billing mechanism observed in the upstream `flexprice` repository. Every rule and claim is backed by verified source code locations.

**Commit studied:** [`31421e9ff62d00a9f4cdded11d0aad5d32a22f4a`](https://github.com/flexprice/flexprice/tree/31421e9ff62d00a9f4cdded11d0aad5d32a22f4a) (Flexprice `main`, 3 Oct 2026). Every `path:line` in `docs/` refers to this commit.

---

## 1. Tech Stack, Commands & Environment Variables

- **Language & Runtime**: Go 1.27.1
  * Evidence: `go.mod:3` [Confirmed]
- **Web & Routing Framework**: Gin Web Framework v1.12.0
  * Evidence: `go.mod:26` [Confirmed]
- **Relational ORM & Schema Engine**: Ent ORM v0.14.6 with PostgreSQL driver (`github.com/lib/pq` v1.12.3)
  * Evidence: `go.mod:7` [Confirmed], `go.mod:34` [Confirmed]
- **OLAP Analytical Storage**: ClickHouse Go driver (`github.com/ClickHouse/clickhouse-go/v2` v2.48.0)
  * Evidence: `go.mod:8` [Confirmed]
- **Distributed Cache & Locking**: Redis (`github.com/redis/go-redis/v9` v9.22.0)
  * Evidence: `go.mod:39` [Confirmed]
- **Message Broker & Pub/Sub**: Apache Kafka via Watermill Kafka (`github.com/ThreeDotsLabs/watermill-kafka/v2` v2.5.0)
  * Evidence: `go.mod:12` [Confirmed]
- **Orchestration & Background Workflows**: Temporal Go SDK v1.49.0
  * Evidence: `go.mod:68` [Confirmed]
- **Decimal Math Library**: Shopspring Decimal v1.4.0 (arbitrary-precision fixed-point math)
  * Evidence: `go.mod:43` [Confirmed]
- **Local Run Commands**:
  * Build & run server: `make run` (alias of `run-server`) executing `go run cmd/server/main.go`
    * Evidence: `Makefile:65-66,93` [Confirmed]
  * Run all-in-one locally with `.env.local` loaded: `make run-local` (`FLEXPRICE_DEPLOYMENT_MODE=local`)
    * Evidence: `Makefile:134-136` [Confirmed]
  * Run Postgres migrations: `make migrate-up` (versioned SQL via `scripts/migrations/apply.sh`) or `make migrate-local` (Ent schema migration)
    * Evidence: `Makefile:242-243`, `Makefile:174-176` [Confirmed]
  * Run docker infrastructure (Postgres, Kafka, ClickHouse, Redis, Temporal, plus API/consumer/worker): `make up` → `docker compose up -d --build`
    * Evidence: `Makefile:57-58` [Confirmed], `docker-compose.yml:1-247` [Confirmed]
- **Key Environment Variable Names** (Viper, prefix `FLEXPRICE_`, `.` → `_`):
  * Database: `FLEXPRICE_POSTGRES_HOST`, `FLEXPRICE_POSTGRES_PORT`, `FLEXPRICE_POSTGRES_USER`, `FLEXPRICE_POSTGRES_PASSWORD`, `FLEXPRICE_POSTGRES_DBNAME`, `FLEXPRICE_POSTGRES_SSLMODE`
  * OLAP Analytics: `FLEXPRICE_CLICKHOUSE_ADDRESS`, `FLEXPRICE_CLICKHOUSE_USERNAME`, `FLEXPRICE_CLICKHOUSE_PASSWORD`, `FLEXPRICE_CLICKHOUSE_DATABASE`
  * Messaging: `FLEXPRICE_KAFKA_BROKERS`, `FLEXPRICE_KAFKA_TOPIC`, `FLEXPRICE_KAFKA_TOPIC_LAZY`, `FLEXPRICE_KAFKA_CONSUMER_GROUP`
  * Temporal: `FLEXPRICE_TEMPORAL_ENABLED`, `FLEXPRICE_TEMPORAL_ADDRESS`
  * Evidence: `.env.local:35-61,123-124` [Confirmed]
  * Cache: Redis is not set in `.env.local`; it is read from the `redis:` block of `config.yaml`, so the derived names are `FLEXPRICE_REDIS_HOST`, `FLEXPRICE_REDIS_PORT`, `FLEXPRICE_REDIS_USERNAME`, `FLEXPRICE_REDIS_PASSWORD`, `FLEXPRICE_REDIS_DB`
  * Evidence: `internal/config/config.yaml:186-191` [Confirmed], `internal/config/config.go:1122-1126` [Confirmed]

---

## 2. Hard-Core Billing Rules & Formulas

### A. Event Deduplication & Storage
- **Event Identifier**: Events are uniquely identified by `event.ID` (`id`), populated from `event_id` in the ingestion request or auto-generated as UUID/ULID.
  * Evidence: `internal/ee/service/event.go:83` [Confirmed], `internal/domain/events/model.go:16` [Confirmed]
- **Redis Dedup Key**: Distributed cache lock uses key `event:<event_id>` with 24-hour TTL (`eventDeduplicationLockTTL = 24 * time.Hour`). If `SetNX` fails, consumer drops event with log `event already processed, skipping`.
  * Evidence: `internal/ee/service/meter_usage_tracking.go:434-445` [Confirmed]
- **ClickHouse Storage Engine**: Table `events` and `meter_usage` use engine `ReplacingMergeTree(ingested_at)` ordered by `(tenant_id, environment_id, timestamp, id)`.
  * Evidence: `migrations/clickhouse/000001_create_events_table.up.sql:17-20` [Confirmed], `migrations/clickhouse/000002_create_meter_usage_table.up.sql:19-22` [Confirmed]
- **ClickHouse Query Finalization**: Usage queries append `FINAL` modifier (`SETTINGS do_not_merge_across_partitions_select_final = 1`) to collapse duplicate rows.
  * Evidence: `internal/repository/clickhouse/meter_usage_query_builder.go:253-259` [Confirmed]
- **Unique Count Hash**: `COUNT_UNIQUE` aggregations derive SHA-256 `unique_hash` from designated attributes, checking `unique_hash != ''`.
  * Evidence: `internal/repository/clickhouse/meter_usage.go:103-120` [Confirmed], `internal/repository/clickhouse/meter_usage_query_builder.go:191-193` [Confirmed]

### B. Meter Aggregations & Window Slicing
- **Aggregation Types**: Supports `COUNT`, `SUM`, `AVG`, `COUNT_UNIQUE`, `LATEST`, `SUM_WITH_MULTIPLIER`, `MAX`, `WEIGHTED_SUM`, and CEL expressions.
  * Evidence: `internal/types/aggregation.go:8-15` [Confirmed], `internal/domain/meter/model.go:56-65` [Confirmed]
- **Window Intervals**: Formatted using ClickHouse date helpers: `toStartOfHour`, `toStartOfDay`, `toStartOfWeek` (Monday start), and `toStartOfMonth`. When a billing anchor day is set, timestamps shift by `anchorDay - 1` days, snap to month start, and shift back.
  * Evidence: `internal/repository/clickhouse/aggregators.go:180-228` [Confirmed]
- **Window Boundaries**: Half-open intervals: `timestamp >= period_start AND timestamp < period_end`.
  * Evidence: `internal/repository/clickhouse/meter_usage_query_builder.go:181-187` [Confirmed]
- **Late Arrivals**: Late events bucket by event `timestamp` (not ingestion time). Draft invoices recompute usage via `ComputeInvoice`; finalized invoices are immutable.
  * Evidence: `internal/ee/service/invoice.go:500-501,580-608` [Confirmed], `internal/ee/service/invoice.go:1123-1145` [Confirmed]

### C. Tiered Pricing & Unit Conversions
- **Tier Modes**:
  * Volume (`BILLING_TIER_VOLUME`): Total consumption maps to a single bracket; entire quantity is billed at that tier's unit amount + flat fee.
  * Slab (`BILLING_TIER_SLAB`): Usage is partitioned across progressive brackets. Each bracket bills the slice falling inside its bounds.
  * Evidence: `internal/ee/service/price.go:1163-1232` [Confirmed], `internal/domain/price/model.go:23-24` [Confirmed]
- **Boundary Inclusivity**: `UpTo` is strictly inclusive (`quantity <= UpTo`). E.g., consumption of exactly 1,000 units with `UpTo = 1000` remains in that tier.
  * Evidence: `internal/domain/price/model.go:348-352` [Confirmed], `internal/ee/service/price.go:1179` [Confirmed]
- **Tier Amount Formula**:
  $$\text{TierCost} = (\text{Quantity} \times \text{UnitAmount}) + \text{FlatAmount}$$
  * Evidence: `internal/domain/price/model.go:282-288` [Confirmed], `internal/ee/service/price.go:1191-1192,1222-1224` [Confirmed]
- **Unit Conversion (Packaging)**: Evaluated before tier matching using `TransformQuantity`:
  $$\text{BilledUnits} = \text{round}\left(\frac{\text{RawUnits}}{\text{DivideBy}}\right)$$
  Where round is `up` (ceiling), `down` (floor), or `none`.
  * Evidence: `internal/domain/price/model.go:320-344` [Confirmed]
- **Precision & Rounding**: Intermediate math keeps arbitrary-precision decimals. Final rounding uses half-up rounding to the currency's precision (`amount.Round(currencyPrecision)`). USD/EUR/INR use 2 decimal places.
  * Evidence: `internal/domain/price/model.go:308-310` [Confirmed], `internal/types/currency.go:11-50,72-76,124-141` [Confirmed]

### D. Proration & Mid-Period Changes
- **Second-Based Coefficient**:
  $$\text{totalSeconds} = \text{periodEnd} - \text{periodStart}$$
  $$\text{remainingSeconds} = \max(0, \text{periodEnd} - \text{prorationDate})$$
  $$\text{coefficient} = \frac{\text{remainingSeconds}}{\text{totalSeconds}}$$
  * Evidence: `internal/domain/proration/coefficient_helper.go:22-35` [Confirmed]
- **Day-Based Coefficient**:
  $$\text{totalDays} = \text{daysBetween}(\text{periodStart}, \text{periodEnd}) + 1$$
  $$\text{remainingDays} = \max(0, \text{daysBetween}(\text{prorationDate}, \text{periodEnd}) + 1)$$
  $$\text{coefficient} = \frac{\text{remainingDays}}{\text{totalDays}}$$
  * Evidence: `internal/domain/proration/coefficient_helper.go:36-56` [Confirmed]
  * **Original uses +1; our rebuild uses no +1, per PRD.** The `+1` above is what the upstream code does. Applied to the PRD.md section 5 Killer Test 2 cycle it yields $16/31$, but PRD.md section 5 requires exactly $15/30 = 0.5000$, and the PRD.md section 6 case 3 edge cases agree with the no-`+1` form (Day 1 $\to 30/30$; Day 30 $\to 1/30$). The rebuild therefore computes $\text{coefficient} = \frac{\text{remainingDays}}{\text{totalDays}}$ with plain floor-day differences and no `+1`.
- **Credit, Charge & Net Calculations**:
  $$\text{CreditAmount} = (\text{OldPrice} \times \text{OldQuantity}) \times \text{coefficient}$$
  $$\text{ChargeAmount} = (\text{NewPrice} \times \text{NewQuantity}) \times \text{coefficient}$$
  $$\text{NetAmount} = \text{ChargeAmount} - \text{CreditAmount}$$
  * Evidence: `internal/domain/proration/calculator.go:95-115,129-144` [Confirmed]
- **Opposing Line Items**: Settlement generates two line items on the settlement invoice: a negative credit line for old plan unused time and a positive debit line for new plan remaining time.
  * Evidence: `internal/ee/service/line_item_proration.go:404-435,549-589` [Confirmed]

### E. Invoicing Pipeline & Idempotency
- **Invoice Assembly**: Assembled by evaluating fixed charges (`CalculateFixedCharges`) and querying usage charges from ClickHouse (`CalculateMeterUsageCharges`).
  * Evidence: `internal/ee/service/billing.go:50-85` [Confirmed], `internal/ee/service/billing_meter_usage.go:92-165` [Confirmed]
- **Idempotency Guarantees**:
  * PostgreSQL unique index `idx_tenant_environment_idempotency_key_unique` on `(tenant_id, environment_id, idempotency_key)`
  * PostgreSQL index `idx_subscription_period_unique` on `(subscription_id, period_start, period_end)` — **despite its name this is a plain, non-unique index** (no `.Unique()` in the Ent schema; the migration emits `CREATE INDEX`, not `CREATE UNIQUE INDEX`), so the database does not stop two non-voided invoices for the same subscription period. Our rebuild makes it a real `UNIQUE` index (`app/db/models.py:138-142`).
    * Evidence: `ent/schema/invoice.go:289-291` [Confirmed], `migrations/versioned/postgres/20260819000000_baseline.sql:160` [Confirmed]
  * Finalization row lock `GetForUpdate` prevents concurrent duplicate invoice number assignment.
  * Evidence: `ent/schema/invoice.go:285-292` [Confirmed], `internal/ee/service/invoice.go:247-259,1127-1135` [Confirmed]

---

## 3. End-to-End Traces

### Trace A: Usage Ingestion to Invoice Line
1. Client issues `POST /v1/events` -> `internal/api/v1/events.go:54-74` [Confirmed]
2. Handled by `eventService.CreateEvent` -> publishes to Kafka topic `events` -> `internal/ee/service/event.go:68-84` [Confirmed]
3. Handler returns HTTP 202 Accepted with `event_id` -> `internal/api/v1/events.go:81` [Confirmed]
4. Consumer triggers `meterUsageTrackingService.processEvent` -> sets Redis lock `event:<id>` (24h) -> `internal/ee/service/meter_usage_tracking.go:434-445` [Confirmed]
5. Matches active meters by event name -> extracts quantity -> generates `unique_hash` -> `internal/ee/service/meter_usage_tracking.go:462-508` [Confirmed]
6. Bulk inserts records into ClickHouse `meter_usage` -> `internal/ee/service/meter_usage_tracking.go:521-524` [Confirmed]
7. During cycle billing, `CalculateMeterUsageCharges` queries ClickHouse with `FINAL` -> `internal/ee/service/billing_meter_usage.go:92-165` [Confirmed]
8. Evaluates tiered cost via `priceService.CalculateCost` -> `internal/ee/service/price.go:1163-1232` [Confirmed]
9. Invoice transaction locks invoice row (`GetForUpdate`) and calls `reconcileLineItems` to write to `invoice_line_items` -> `internal/ee/service/invoice.go:580-640` [Confirmed]

### Trace B: Mid-Period Upgrade & Proration Settlement
1. Client calls `POST /v1/subscriptions/:id/change/v2/execute` -> `internal/api/v1/subscription_plan_change.go:65-78` [Confirmed]
2. `subscriptionService.ExecutePlanChange` forwards to `executePlanChangeAt` -> begins DB transaction and acquires `FOR UPDATE` lock -> `internal/ee/service/subscription_change_v2.go:855-868` [Confirmed]
3. `resolvePlanChange` validates compatibility and currency parity -> `internal/ee/service/subscription_change_v2.go:64-100` [Confirmed]
4. `lineItemProrationService.Compute` reads billed amounts from PostgreSQL -> computes proration coefficient, unused credit, and new charge -> `internal/ee/service/line_item_proration.go:200-260` [Confirmed]
5. Updates line items (`end_date = effectiveAt` on old, `start_date = effectiveAt` on new) and swaps `plan_id` -> `internal/ee/service/subscription_change_v2.go:880-898` [Confirmed]
6. Calls `settlePlanChange` -> `lineItemProrationService.Settle` builds a netted invoice with credit and debit lines -> calls `invoiceService.CreateInvoice` -> `internal/ee/service/subscription_change_v2.go:1204-1266` [Confirmed], `internal/ee/service/line_item_proration.go:303-360` [Confirmed]
7. Writes invoice and line items to PostgreSQL; transaction commits; returns 200 OK -> `internal/ee/service/invoice.go:220-300` [Confirmed]
