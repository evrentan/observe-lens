# MySQL / MariaDB notes

Queries run inside `SET SESSION TRANSACTION READ ONLY; START TRANSACTION
READ ONLY; ... ROLLBACK;` and the same guard as postgres applies (SELECT,
WITH — MySQL 8+ — SHOW, DESCRIBE, EXPLAIN, TABLE, VALUES).

## Catalog queries

- Databases: `show databases`
- Tables: `db.py tables [--schema <db>]`
- Columns: `db.py columns <table> --schema <db>` or `describe <table>`
- Process list: `show processlist` (SHOW is allowed)
- Sizes: `select table_name, round(data_length/1048576) mb from information_schema.tables where table_schema='<db>'`

## JSON columns

`payload->>'$.status'` (note the `$.` prefix), `json_extract(payload,'$.items')`,
`json_table()` on MySQL 8 for exploding arrays.

## MySQL-specific notes

- `SHOW`/`DESCRIBE`/`EXPLAIN` are native and allowed; `EXPLAIN ANALYZE`
  (executes the query on MySQL 8.0.18+) is refused like postgres.
- Backslash commands (`\!`, `\s`) are refused — they can run shell.
- `INTO OUTFILE`/`LOAD_FILE` are refused — server-side file access.
- Time zones: MySQL `timestamp` columns convert per session TZ; `datetime`
  does not. `select @@global.time_zone, @@session.time_zone` if a value
  looks shifted.
- MariaDB differences: `current_user()` works; some information_schema
  columns differ slightly — `describe`/`columns` output is authoritative.
