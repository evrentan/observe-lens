# Pipelines setup

## Config fields

| Provider | Required | Optional | Secret keys |
| --- | --- | --- | --- |
| jenkins | `url` | `environment`, `description`, `timeout`, `ca_file` | `username`, `token` |
| github | `url` (default `https://api.github.com`; GHES `https://host/api/v3`) | `repo` (`owner/name`), `web_url`, `environment`, `timeout` | `token` |

## Creating tokens

- **Jenkins**: user → Configure → "API Token" → Add new token. Any user API
  token works; a dedicated read-only account is better. There is no
  read-only token scope — restrict what the *user* can see instead.
- **GitHub fine-grained** (recommended): Settings → Developer settings →
  Fine-grained tokens → select the repo(s) → permissions:
  Actions: **read**, Contents: **read**, Metadata: **read**.
- **GitHub classic**: `repo` scope for private repositories,
  `public_repo` for public-only.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| HTTP 401 | token wrong/expired; re-run `config add` in your terminal |
| HTTP 403 | token lacks a scope (GitHub: Actions/Contents read) or the Jenkins user cannot see the job |
| HTTP 404 on a URL | job path spelling — try `job/F/job/J`, or the job moved folders |
| `no repository` | set `"repo": "owner/name"` in the instance or pass `--repo` |
| whoami fails on GitHub but repos work | fine-grained tokens have no `/user`; expected — the fallback message confirms access |
| TLS error | set `ca_file` to your corporate CA's PEM bundle (`verify_tls: false` is not supported) |
| empty `runs` | wrong workflow name — use `runs all --limit 20` to list everything |
