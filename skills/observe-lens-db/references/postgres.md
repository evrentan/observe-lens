# PostgreSQL notes

Queries run through psql with `default_transaction_read_only=on` and a
60s statement timeout; the guard only lets SELECT/WITH/SHOW/DESCRIBE/
EXPLAIN/TABLE/VALUES through.

## Catalog queries

- Schemas: `select schema_name from information_schema.schemata`
- Tables: `db.py tables --schema <s> [--filter <text>]`
- Columns: `db.py columns <table> --schema <s>`
- Sizes: `select relname, pg_size_pretty(pg_total_relation_size(oid)) from pg_class where relkind='r' order by 2 desc limit 20`
- Running activity (read-only view): `select pid, state, query_start, left(query,80) from pg_stat_activity`

## JSON columns

`payload->>'status'`, `payload->'items'->0->>'id'`,
`jsonb_path_exists(payload, '$.items[*] ? (@.id == "X")')`. Filter on a
JSON field the same way: `where payload->>'orderId' = 'A-123'`.

## Time zones

`now()` returns the session TZ (usually UTC on managed instances); stored
`timestamp` columns may be `without time zone` — compare against the row's
own clock, and convert with `at time zone 'UTC'` when in doubt.

## Common errors

- `permission denied for table` → the read-only user lacks SELECT on that
  schema; ask the DBA to grant it.
- `canceling statement due to statement timeout` → narrow the WHERE or add
  an index-friendly filter (id or date), then retry.
- `SSL required` → set `"sslmode": "require"` in the instance.
- `connection refused` → tunnel down (`tunnel.py status`) or wrong
  host/port.
