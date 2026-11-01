# Telemetry setup

## Config fields

| Provider | Required | Optional | Secret keys |
| --- | --- | --- | --- |
| grafana | `url` | `org_id`, `datasources` (`logs`/`metrics`/`traces` uids), `environment`, `timeout`, `ca_file` | `token` |
| prometheus / loki / tempo | `url` | `tenant` (`X-Scope-OrgID`), `environment`, `timeout` | `token` or `username`+`password` (all optional) |

## Grafana service account

Administration → Users and access → Service accounts → Add service account
with role **Viewer** → Add token. Add it with
`telemetry.py config add grafana-prod --provider grafana --url https://grafana.example.com`.
Viewer can query datasources, search dashboards and read alerts — exactly
what this skill does.

Datasource uids come from `telemetry.py datasources`; without them the
script auto-picks by type and tells you which it picked.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| HTTP 401 | token wrong/expired — regenerate the service-account token |
| HTTP 403 | service account role below Viewer, or org_id mismatch |
| `no datasource configured` | set `datasources.<signal>` uid or `--ds <uid>` |
| empty logs for a known incident | wrong label names (`labels` lists them), retention expired, or the line needs `|= "<id>"` in the selector |
| traces empty but logs show the request | sampling — the trace may not have been kept; rely on logs |
| direct loki 401 | set `tenant` (X-Scope-OrgID) or credentials |
