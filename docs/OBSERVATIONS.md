# System Observations & Reverse-Engineered Findings: Flexprice Core

This document catalogs every verified architectural claim, code path, and core billing mechanism observed in the upstream `flexprice` repository. Every rule and claim is backed by verified source code locations.

---

## 1. Tech Stack, Commands & Environment Variables

- **Language & Runtime**: Go version 1.24.0
  * Evidence: [go.mod:3](file:///d:/Reverse-Flexprice/flexprice/go.mod#L3) [Confirmed]
- **Web & Routing Framework**: Gin Web Framework v1.10.0
  * Evidence: [go.mod:23](file:///d:/Reverse-Flexprice/flexprice/go.mod#L23) [Confirmed]
- **Relational ORM & Schema Engine**: Ent ORM v0.14.1 with PostgreSQL driver (`github.com/lib/pq` v1.10.9)
  * Evidence: [go.mod:33](file:///d:/Reverse-Flexprice/flexprice/go.mod#L33) [Confirmed], [go.mod:60](file:///d:/Reverse-Flexprice/flexprice/go.mod#L60) [Confirmed]
- **OLAP Analytical Storage**: ClickHouse Go driver (`github.com/ClickHouse/clickhouse-go/v2` v2.30.0)
  * Evidence: [go.mod:15](file:///d:/Reverse-Flexprice/flexprice/go.mod#L15) [Confirmed]
- **Distributed Cache & Locking**: Redis (`github.com/redis/go-redis/v9` v9.7.0)
  * Evidence: [go.mod:69](file:///d:/Reverse-Flexprice/flexprice/go.mod#L69) [Confirmed]
- **Message Broker & Pub/Sub**: Apache Kafka using Watermill Kafka v3.0.6
  * Evidence: [go.mod:38](file:///d:/Reverse-Flexprice/flexprice/go.mod#L38) [Confirmed]
- **Orchestration & Background Workflows**: Temporal Go SDK v1.33.0
  * Evidence: [go.mod:39](file:///d:/Reverse-Flexprice/flexprice/go.mod#L39) [Confirmed]
- **Decimal Math Library**: Shopspring Decimal v1.4.0 (arbitrary-precision fixed-point math)
  * Evidence: [go.mod:71](file:///d:/Reverse-Flexprice/flexprice/go.mod#L71) [Confirmed]
- **Local Run Commands**:
  * Build & run server: `make run` executing `go run cmd/server/main.go`
    * Evidence: [Makefile:61-63](file:///d:/Reverse-Flexprice/flexprice/Makefile#L61-L63) [Confirmed]
  * Run migrations: `make migrate-up` and `make clickhouse-migrate-up`
    * Evidence: [Makefile:82-90](file:///d:/Reverse-Flexprice/flexprice/Makefile#L82-L90) [Confirmed]
  * Run docker infrastructure (Postgres, ClickHouse, Redis, Kafka): `docker-compose up -d`
    * Evidence: [docker-compose.yml:1-125](file:///d:/Reverse-Flexprice/flexprice/docker-compose.yml#L1-L125) [Confirmed]
- **Key Environment Variable Names**:
  * Database: `FLEXPRICE_POSTGRES_HOST`, `FLEXPRICE_POSTGRES_PORT`, `FLEXPRICE_POSTGRES_DB`, `FLEXPRICE_POSTGRES_USER`, `FLEXPRICE_POSTGRES_PASSWORD`
  * OLAP Analytics: `FLEXPRICE_CLICKHOUSE_ADDRESS`, `FLEXPRICE_CLICKHOUSE_DATABASE`, `FLEXPRICE_CLICKHOUSE_USER`, `FLEXPRICE_CLICKHOUSE_PASSWORD`
  * Cache: `FLEXPRICE_REDIS_ADDRESS`, `FLEXPRICE_REDIS_PASSWORD`, `FLEXPRICE_REDIS_DB`
  * Messaging: `FLEXPRICE_KAFKA_BROKERS`, `FLEXPRICE_KAFKA_TOPIC_EVENTS`, `FLEXPRICE_KAFKA_CONSUMER_GROUP`
  * Temporal: `FLEXPRICE_TEMPORAL_HOST`, `FLEXPRICE_TEMPORAL_NAMESPACE`
  * Evidence: [.env.local:1-130](file:///d:/Reverse-Flexprice/flexprice/.env.local#L1-L130) [Confirmed]

---

## 2. Hard-Core Billing Rules & Formulas

### A. Event Deduplication & Storage
- **Event Identifier**: Events are uniquely identified by `event.ID` (`id`), populated from `event_id` in the ingestion request or auto-generated as UUID/ULID.
  * Evidence: [internal/ee/service/event.go:83](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/event.go#L83) [Confirmed], [internal/domain/events/model.go:16](file:///d:/Reverse-Flexprice/flexprice/internal/domain/events/model.go#L16) [Confirmed]
- **Redis Dedup Key**: Distributed cache lock uses key `event:<event_id>` with 24-hour TTL (`eventDeduplicationLockTTL = 24 * time.Hour`). If `SetNX` fails, consumer drops event with log `event already processed, skipping`.
  * Evidence: [internal/ee/service/meter_usage_tracking.go:434-445](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/meter_usage_tracking.go#L434-L445) [Confirmed]
- **ClickHouse Storage Engine**: Table `events` and `meter_usage` use engine `ReplacingMergeTree(ingested_at)` ordered by `(tenant_id, environment_id, timestamp, id)`.
  * Evidence: [migrations/clickhouse/000001_create_events_table.up.sql:17-20](file:///d:/Reverse-Flexprice/flexprice/migrations/clickhouse/000001_create_events_table.up.sql#L17-L20) [Confirmed], [migrations/clickhouse/000002_create_meter_usage_table.up.sql:19-22](file:///d:/Reverse-Flexprice/flexprice/migrations/clickhouse/000002_create_meter_usage_table.up.sql#L19-L22) [Confirmed]
- **ClickHouse Query Finalization**: Usage queries append `FINAL` modifier (`SETTINGS do_not_merge_across_partitions_select_final = 1`) to collapse duplicate rows.
  * Evidence: [internal/repository/clickhouse/meter_usage_query_builder.go:253-259](file:///d:/Reverse-Flexprice/flexprice/internal/repository/clickhouse/meter_usage_query_builder.go#L253-L259) [Confirmed]
- **Unique Count Hash**: `COUNT_UNIQUE` aggregations derive SHA-256 `unique_hash` from designated attributes, checking `unique_hash != ''`.
  * Evidence: [internal/repository/clickhouse/meter_usage.go:103-120](file:///d:/Reverse-Flexprice/flexprice/internal/repository/clickhouse/meter_usage.go#L103-L120) [Confirmed], [internal/repository/clickhouse/meter_usage_query_builder.go:191-193](file:///d:/Reverse-Flexprice/flexprice/internal/repository/clickhouse/meter_usage_query_builder.go#L191-L193) [Confirmed]

### B. Meter Aggregations & Window Slicing
- **Aggregation Types**: Supports `COUNT`, `SUM`, `AVG`, `COUNT_UNIQUE`, `LATEST`, `SUM_WITH_MULTIPLIER`, `MAX`, `WEIGHTED_SUM`, and CEL expressions.
  * Evidence: [internal/types/aggregation.go:8-15](file:///d:/Reverse-Flexprice/flexprice/internal/types/aggregation.go#L8-L15) [Confirmed], [internal/domain/meter/model.go:56-65](file:///d:/Reverse-Flexprice/flexprice/internal/domain/meter/model.go#L56-L65) [Confirmed]
- **Window Intervals**: Formatted using ClickHouse date helpers: `toStartOfHour`, `toStartOfDay`, `toStartOfWeek` (Monday start), and `toStartOfMonth`. When a billing anchor day is set, timestamps shift by `anchorDay - 1` days, snap to month start, and shift back.
  * Evidence: [internal/repository/clickhouse/aggregators.go:180-228](file:///d:/Reverse-Flexprice/flexprice/internal/repository/clickhouse/aggregators.go#L180-L228) [Confirmed]
- **Window Boundaries**: Half-open intervals: `timestamp >= period_start AND timestamp < period_end`.
  * Evidence: [internal/repository/clickhouse/meter_usage_query_builder.go:181-187](file:///d:/Reverse-Flexprice/flexprice/internal/repository/clickhouse/meter_usage_query_builder.go#L181-L187) [Confirmed]
- **Late Arrivals**: Late events bucket by event `timestamp` (not ingestion time). Draft invoices recompute usage via `ComputeInvoice`; finalized invoices are immutable.
  * Evidence: [internal/ee/service/invoice.go:500-501,580-608](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/invoice.go#L500-L501) [Confirmed], [internal/ee/service/invoice.go:1123-1145](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/invoice.go#L1123-L1145) [Confirmed]

### C. Tiered Pricing & Unit Conversions
- **Tier Modes**:
  * Volume (`BILLING_TIER_VOLUME`): Total consumption maps to a single bracket; entire quantity is billed at that tier's unit amount + flat fee.
  * Slab (`BILLING_TIER_SLAB`): Usage is partitioned across progressive brackets. Each bracket bills the slice falling inside its bounds.
  * Evidence: [internal/ee/service/price.go:1163-1232](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/price.go#L1163-L1232) [Confirmed], [internal/domain/price/model.go:23-24](file:///d:/Reverse-Flexprice/flexprice/internal/domain/price/model.go#L23-L24) [Confirmed]
- **Boundary Inclusivity**: `UpTo` is strictly inclusive (`quantity <= UpTo`). E.g., consumption of exactly 1,000 units with `UpTo = 1000` remains in that tier.
  * Evidence: [internal/domain/price/model.go:348-352](file:///d:/Reverse-Flexprice/flexprice/internal/domain/price/model.go#L348-L352) [Confirmed], [internal/ee/service/price.go:1179](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/price.go#L1179) [Confirmed]
- **Tier Amount Formula**:
  $$\text{TierCost} = (\text{Quantity} \times \text{UnitAmount}) + \text{FlatAmount}$$
  * Evidence: [internal/domain/price/model.go:282-288](file:///d:/Reverse-Flexprice/flexprice/internal/domain/price/model.go#L282-L288) [Confirmed], [internal/ee/service/price.go:1191-1192,1222-1224](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/price.go#L1191-L1192) [Confirmed]
- **Unit Conversion (Packaging)**: Evaluated before tier matching using `TransformQuantity`:
  $$\text{BilledUnits} = \text{round}\left(\frac{\text{RawUnits}}{\text{DivideBy}}\right)$$
  Where round is `up` (ceiling), `down` (floor), or `none`.
  * Evidence: [internal/domain/price/model.go:320-344](file:///d:/Reverse-Flexprice/flexprice/internal/domain/price/model.go#L320-L344) [Confirmed]
- **Precision & Rounding**: Intermediate math keeps arbitrary-precision decimals. Final rounding uses half-up rounding to the currency's precision (`amount.Round(currencyPrecision)`). USD/EUR/INR use 2 decimal places.
  * Evidence: [internal/domain/price/model.go:308-310](file:///d:/Reverse-Flexprice/flexprice/internal/domain/price/model.go#L308-L310) [Confirmed], [internal/types/currency.go:11-50,72-76,124-141](file:///d:/Reverse-Flexprice/flexprice/internal/types/currency.go#L11-L50) [Confirmed]

### D. Proration & Mid-Period Changes
- **Second-Based Coefficient**:
  $$\text{totalSeconds} = \text{periodEnd} - \text{periodStart}$$
  $$\text{remainingSeconds} = \max(0, \text{periodEnd} - \text{prorationDate})$$
  $$\text{coefficient} = \frac{\text{remainingSeconds}}{\text{totalSeconds}}$$
  * Evidence: [internal/domain/proration/coefficient_helper.go:22-35](file:///d:/Reverse-Flexprice/flexprice/internal/domain/proration/coefficient_helper.go#L22-L35) [Confirmed]
- **Day-Based Coefficient**:
  $$\text{totalDays} = \text{daysBetween}(\text{periodStart}, \text{periodEnd}) + 1$$
  $$\text{remainingDays} = \max(0, \text{daysBetween}(\text{prorationDate}, \text{periodEnd}) + 1)$$
  $$\text{coefficient} = \frac{\text{remainingDays}}{\text{totalDays}}$$
  * Evidence: [internal/domain/proration/coefficient_helper.go:36-56](file:///d:/Reverse-Flexprice/flexprice/internal/domain/proration/coefficient_helper.go#L36-L56) [Confirmed]
- **Credit, Charge & Net Calculations**:
  $$\text{CreditAmount} = (\text{OldPrice} \times \text{OldQuantity}) \times \text{coefficient}$$
  $$\text{ChargeAmount} = (\text{NewPrice} \times \text{NewQuantity}) \times \text{coefficient}$$
  $$\text{NetAmount} = \text{ChargeAmount} - \text{CreditAmount}$$
  * Evidence: [internal/domain/proration/calculator.go:95-115,129-144](file:///d:/Reverse-Flexprice/flexprice/internal/domain/proration/calculator.go#L95-L115) [Confirmed]
- **Opposing Line Items**: Settlement generates two line items on the settlement invoice: a negative credit line for old plan unused time and a positive debit line for new plan remaining time.
  * Evidence: [internal/ee/service/line_item_proration.go:404-435,549-589](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/line_item_proration.go#L404-L435) [Confirmed]

### E. Invoicing Pipeline & Idempotency
- **Invoice Assembly**: Assembled by evaluating fixed charges (`CalculateFixedCharges`) and querying usage charges from ClickHouse (`CalculateMeterUsageCharges`).
  * Evidence: [internal/ee/service/billing.go:50-85](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/billing.go#L50-L85) [Confirmed], [internal/ee/service/billing_meter_usage.go:92-165](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/billing_meter_usage.go#L92-L165) [Confirmed]
- **Idempotency Guarantees**:
  * PostgreSQL unique index `idx_tenant_environment_idempotency_key_unique` on `(tenant_id, environment_id, idempotency_key)`
  * PostgreSQL unique index `idx_subscription_period_unique` on `(subscription_id, period_start, period_end)`
  * Finalization row lock `GetForUpdate` prevents concurrent duplicate invoice number assignment.
  * Evidence: [ent/schema/invoice.go:285-292](file:///d:/Reverse-Flexprice/flexprice/ent/schema/invoice.go#L285-L292) [Confirmed], [internal/ee/service/invoice.go:247-259,1127-1135](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/invoice.go#L247-L259) [Confirmed]

---

## 3. End-to-End Traces

### Trace A: Usage Ingestion to Invoice Line
1. Client issues `POST /v1/events` -> [internal/api/v1/events.go:54-74](file:///d:/Reverse-Flexprice/flexprice/internal/api/v1/events.go#L54-L74) [Confirmed]
2. Handled by `eventService.CreateEvent` -> publishes to Kafka topic `events` -> [internal/ee/service/event.go:68-84](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/event.go#L68-L84) [Confirmed]
3. Handler returns HTTP 202 Accepted with `event_id` -> [internal/api/v1/events.go:81](file:///d:/Reverse-Flexprice/flexprice/internal/api/v1/events.go#L81) [Confirmed]
4. Consumer triggers `meterUsageTrackingService.processEvent` -> sets Redis lock `event:<id>` (24h) -> [internal/ee/service/meter_usage_tracking.go:434-445](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/meter_usage_tracking.go#L434-L445) [Confirmed]
5. Matches active meters by event name -> extracts quantity -> generates `unique_hash` -> [internal/ee/service/meter_usage_tracking.go:462-508](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/meter_usage_tracking.go#L462-L508) [Confirmed]
6. Bulk inserts records into ClickHouse `meter_usage` -> [internal/ee/service/meter_usage_tracking.go:521-524](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/meter_usage_tracking.go#L521-L524) [Confirmed]
7. During cycle billing, `CalculateMeterUsageCharges` queries ClickHouse with `FINAL` -> [internal/ee/service/billing_meter_usage.go:92-165](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/billing_meter_usage.go#L92-L165) [Confirmed]
8. Evaluates tiered cost via `priceService.CalculateCost` -> [internal/ee/service/price.go:1163-1232](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/price.go#L1163-L1232) [Confirmed]
9. Invoice transaction locks invoice row (`GetForUpdate`) and calls `reconcileLineItems` to write to `invoice_line_items` -> [internal/ee/service/invoice.go:580-640](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/invoice.go#L580-L640) [Confirmed]

### Trace B: Mid-Period Upgrade & Proration Settlement
1. Client calls `POST /v1/subscriptions/:id/change/v2/execute` -> [internal/api/v1/subscription_plan_change.go:65-78](file:///d:/Reverse-Flexprice/flexprice/internal/api/v1/subscription_plan_change.go#L65-L78) [Confirmed]
2. `subscriptionService.ExecutePlanChange` forwards to `executePlanChangeAt` -> begins DB transaction and acquires `FOR UPDATE` lock -> [internal/ee/service/subscription_change_v2.go:855-868](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/subscription_change_v2.go#L855-L868) [Confirmed]
3. `resolvePlanChange` validates compatibility and currency parity -> [internal/ee/service/subscription_change_v2.go:64-100](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/subscription_change_v2.go#L64-L100) [Confirmed]
4. `lineItemProrationService.Compute` reads billed amounts from PostgreSQL -> computes proration coefficient, unused credit, and new charge -> [internal/ee/service/line_item_proration.go:200-260](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/line_item_proration.go#L200-L260) [Confirmed]
5. Updates line items (`end_date = effectiveAt` on old, `start_date = effectiveAt` on new) and swaps `plan_id` -> [internal/ee/service/subscription_change_v2.go:880-898](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/subscription_change_v2.go#L880-L898) [Confirmed]
6. Calls `settlePlanChange` -> `lineItemProrationService.Settle` builds a netted invoice with credit and debit lines -> calls `invoiceService.CreateInvoice` -> [internal/ee/service/subscription_change_v2.go:1204-1266](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/subscription_change_v2.go#L1204-L1266) [Confirmed], [internal/ee/service/line_item_proration.go:303-360](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/line_item_proration.go#L303-L360) [Confirmed]
7. Writes invoice and line items to PostgreSQL; transaction commits; returns 200 OK -> [internal/ee/service/invoice.go:220-300](file:///d:/Reverse-Flexprice/flexprice/internal/ee/service/invoice.go#L220-L300) [Confirmed]
