# Grafana notes

## Datasource proxy

All logs/metrics/traces queries go through
`/api/datasources/proxy/uid/<uid>/<path>` so one Grafana token reaches
every datasource: Loki `loki/api/v1/...`, Prometheus `api/v1/...`,
Tempo `api/...`. The instance's `datasources` map (`logs`, `metrics`,
`traces` → uid) pins the choice; without it the script picks by type from
`/api/datasources` (preferring `isDefault`) and says which it picked.
`--ds <uid>` overrides.

## Token

Use a **service account** (Administration → Service accounts) with a token;
a **Viewer** role covers everything this skill does. `X-Grafana-Org-Id`
comes from the instance's `org_id` when set.

## API paths

- `/api/health`, `/api/user`, `/api/org` — whoami fallbacks
- `/api/datasources` — uid/type/default per datasource
- `/api/search?query=<text>&type=dash-db` — dashboard search (absolute URLs
  are base + `url`)
- `/api/prometheus/grafana/api/v1/alerts` — Grafana-managed alert rules
- `/api/alertmanager/grafana/api/v2/alerts` — alertmanager alert instances

## Explore links

`link logs|metrics|traces '<query>' --from --to` prints an Explore URL
(`?schemaVersion=1&panes=<url-encoded JSON>&orgId=<n>`) you can paste into
a browser or ticket — a human sees the same query you ran.

## Direct providers

`prometheus`, `loki` and `tempo` provider instances skip Grafana and hit
the API directly (`api/v1`, `loki/api/v1`, `api`). Optional `token`
(Bearer) or `username`+`password` (Basic); `tenant` adds `X-Scope-OrgID`
for multi-tenant Loki/Tempo/Mimir.
