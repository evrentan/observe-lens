# Adding a CI provider

`pipelines.py` dispatches on the instance's `provider`. To add GitLab CI,
Azure Pipelines or CircleCI:

1. Add the provider's secret keys to `PROVIDER_SECRET_KEYS` and required
   fields to `REQUIRED` in `scripts/pipelines.py` (and in
   `common/_ol_common.py` — keep the four copies in sync with
   `tools/sync_common.py`).
2. Write a `cmd_<provider>(args, ctx)` function mirroring the existing two:
   `whoami`, `runs`, `run`, `log`, `params`, `artifacts`, `artifact`, `get`.
   Keep it GET-only and reuse `ol.get_json`/`ol.http_get` so auth headers
   and error handling stay uniform.
3. Add a URL parser if users paste links (see `GH_RUN_URL_RE` /
   `jenkins_parse_job_run` for the pattern).

## Endpoint mapping cheatsheet

| Concept | GitLab CI | Azure Pipelines | CircleCI |
| --- | --- | --- | --- |
| whoami | `GET /api/v4/user` | `GET {org}/_apis/connectionData` | `GET /api/v2/me` |
| runs | `GET /projects/:id/pipelines` | `GET .../builds?definitions=` | `GET /api/v2/project/:slug/pipeline` |
| run detail | `GET /pipelines/:id` + `/jobs` | `GET .../builds/{id}` + `/timeline` | `GET .../pipeline/:id/workflow` |
| log | `GET /jobs/:id/trace` | `GET .../builds/{id}/logs/{logId}` | job output via `/api/v1.1/project/.../{n}/output` |
| artifacts | `GET /jobs/:id/artifacts` | `GET .../builds/{id}/artifacts` | `GET .../{n}/artifacts` |
| auth header | `PRIVATE-TOKEN:` | Basic with PAT as password | `Circle-Token:` |

Notes: GitLab project is `:id` or url-encoded `group%2Fpath`; Azure devops
APIs need `?api-version=7.1`; CircleCI v2 pipelines still serve v1.1 job
output. Update this file and `references/setup.md` with the provider's
token creation steps when you add it.
