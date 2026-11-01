# Adding a telemetry provider

`telemetry.py` dispatches on the instance `provider`. To add
Elasticsearch/OpenSearch, Datadog, New Relic, CloudWatch or Jaeger:

1. Register secret keys in `PROVIDER_SECRET_KEYS` and required fields in
   `REQUIRED` (`common/_ol_common.py` + `scripts/telemetry.py`, synced by
   `tools/sync_common.py`).
2. Add the provider to `make_ctx` (auth header shape) and route the
   subcommands: map `logs`, `metric`, `metric-range`, `traces`, `trace`,
   `alerts` onto the backend's GET endpoints, reusing `api()`/`sig_api()`.

## Endpoint cheatsheet

| Signal | Elasticsearch/OpenSearch | Datadog | CloudWatch | Jaeger |
| --- | --- | --- | --- | --- |
| logs | `GET /<index>/_search?q=` | `GET /api/v2/logs/events` (POST exists too) | `GET` via `filterLogEvents` (signed) | — |
| metrics | — | `GET /api/v1/query` | `GetMetricData` (signed) | — |
| traces | APM `GET /traces/_search` | `GET /api/v1/apm/traces` | X-Ray `GetTraceSummaries` | `GET /api/traces?service=` |
| trace | `GET /traces/<id>` | span endpoints | `BatchGetTraces` | `GET /api/traces/<id>` |
| auth | Basic/ApiKey header | `DD-API-KEY` + `DD-APPLICATION-KEY` | SigV4 signing | none/Bearer |

Caveats: CloudWatch needs AWS request signing — the stdlib can do SigV4 but
it is the most work; Datadog/New Relic query APIs are POST-shaped, so wrap
them as GET-style query params where the API allows or keep the helper
GET-only and document the limitation. Jaeger's API is the simplest drop-in
(`/api/services`, `/api/traces`). Update `references/setup.md` with the
new provider's fields when you add it.
