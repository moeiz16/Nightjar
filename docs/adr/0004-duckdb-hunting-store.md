# ADR 0004: DuckDB as the hunting store, with a single writer

**Status:** accepted

## Context
Analysts need fast ad-hoc searches and aggregations over millions of normalised events (time buckets, group-bys,
JSON field access); the executive view needs counts and trends. The app database (SQLite/Postgres) is the system
of record for incidents and should not absorb event-scale writes. ClickHouse would be the production choice but
is a server; the laptop budget calls for something embedded.

## Decision
Use **DuckDB 2.0** (embedded, columnar) in `data/hunting.duckdb`, one `events` table with the flattened ECS
columns, `org_id` and the raw JSON. DuckDB allows one read-write process per file, so **the API process is the only
writer**: it runs the `store-writer` durable consumer and appends batches; it also serves searches. Other
processes never open the file while the API runs; training and eval read Parquet snapshots exported by the API or
open the file read-only when the API is stopped. Agents never query DuckDB: detections carry bounded evidence
snapshots in the app database.

Clients never send SQL: searches are structured (`HuntQuery`), compiled to parameterised SQL with a mandatory
`org_id` predicate, a row cap and a timeout.

Verified with `scripts/smoke/store_ml_smoke.py`: 50k events in **0.8 MB**; a time-bucketed aggregation with a
trailing window in **24 ms**; JSON path extraction; parameterised queries; Parquet export/import; read-only reopen.

## Consequences
- Insert path matters: `executemany` managed ~2,300 rows/s while a DataFrame insert managed ~1M rows/s, so the
  writer always batches through a DataFrame/Arrow table.
- DuckDB returns `TIMESTAMPTZ` values through `pytz`; pandas 3 no longer depends on it, so `pytz` is pinned
  explicitly.
- Window functions over aggregates need a CTE (aggregate first, window second); `GROUP BY ALL` cannot see a
  window's `ORDER BY` expression.
- A second API worker process cannot share the file: scale-out means moving the store to ClickHouse or MotherDuck
  behind the same `hunting.api` interface (documented limitation).
