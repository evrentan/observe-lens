# observe-lens configuration

## Layout

```
<config-root>/
  pipelines/   cd/   db/   telemetry/
    config.json            instances (+ db tunnels)
    secrets/<name>.json    per-instance secrets (never commit)
    knowledge/*.md         your notes about your own systems
```

Config root is, in order: `$OBSERVE_LENS_CONFIG_DIR`, else
`$XDG_CONFIG_HOME/observe-lens`, else `~/.config/observe-lens` (same path on
Windows — the scripts do not use `%APPDATA%`). `config path` shows the
resolved paths.

## config.json shape

```json
{"default": "<name>",
 "instances": {"<name>": {"provider": "jenkins|github|argocd|kubernetes|
grafana|prometheus|loki|tempo", "url": "...", "environment": "...",
 "description": "...", "...provider fields", "ca_file": "...",
 "timeout": 60}}}
```

The db area uses `"engine": "postgres|mysql|mongodb"` plus `host`, `port`,
`database`, `user`, `users`, `sslmode`, `auth_source`, `tls`, `options`,
`tunnel` and a top-level `"tunnels"` map. TLS certificates are always
verified. For an internal or self-signed CA, set `ca_file` to its PEM bundle;
`verify_tls: false` is refused, because it would expose your credentials.

## Creating instances

Run these **yourself in your own terminal** — the agent never types secrets:

```
python3 scripts/<area>.py config init
python3 scripts/<area>.py config add <name> --provider <p> --url <u> --default
python3 scripts/<area>.py config add db1 --engine postgres --set host=db.internal --set database=shop --set users=shop_ro
python3 scripts/<area>.py config check
```

`config init` creates the area folder, an **empty** `config.json`
(`{"default": null, "instances": {}}`, plus `"tunnels": {}` for db), a
`config.example.json` next to it for reference, `secrets/` (0700) and a
knowledge template — it never overwrites.

`config add` prompts for each secret key of the provider and writes
`secrets/<name>.json` with 0600 permissions inside a 0700 `secrets/`
folder. `--no-secret` skips that. `--set key=value` sets extra fields;
JSON values work (`--set port=5432`) and comma-separated values become a
list (`--set users=shop_ro,report_ro`).

**db tunnels** are edited into `config.json` directly — copy the
`"tunnels"` block from `config.example.json` and adjust host, key and
forwards.

**Production marker.** An instance counts as production when
`environment` is `prod`, `production`, `prd` or `live`, or when
`"confirm": true` is set (`"confirm": false` overrides the environment
default). The scripts print a `note:` line on stderr when they run
against one, `config list` shows `confirm=yes|no`, and `config check`
warns about instances that merely *look* like production without either
field set.

**Plain http.** When an instance `url` is `http://` to a non-localhost
host and a credential is configured, the scripts print a warning — it is
allowed (local Prometheus etc.), just flagged.

## Secret resolution order

For each secret key K the scripts try, first hit wins:

1. Environment variable `OBSERVE_LENS_<AREA>_<INSTANCE>_<KEY>` (uppercase;
   `-` and other non-alphanumerics become `_`), e.g.
   `OBSERVE_LENS_PIPELINES_CI_MAIN_TOKEN`.
2. Instance `"secret_env": {"K": "ENV_NAME"}` — read another variable you
   already export.
3. Instance `"secret_command": {"K": ["argv", "..."]}` — run a command
   (no shell) and take its stdout. Examples:

   ```json
   "secret_command": {"token": ["security", "find-generic-password", "-s", "my-jenkins", "-w"]}
   "secret_command": {"token": ["secret-tool", "lookup", "service", "jenkins"]}
   "secret_command": {"token": ["pass", "show", "ci/jenkins"]}
   "secret_command": {"token": ["op", "read", "op://Private/Jenkins/token"]}
   "secret_command": {"token": ["gh", "auth", "token"]}
   "secret_command": {"token": ["powershell", "-Command", "(Get-Secret -Name jenkins -AsPlainText)"]}
   ```

   On Windows PowerShell with SecretManagement:
   `["powershell", "-Command", "(Get-Secret -Name jenkins-token -AsPlainText)"]`.
4. `secrets/<instance>.json` — flat `{"key": "value"}` written by
   `config add`. On POSIX the scripts warn if the file or the `secrets/`
   folder is group/world readable; fix with `chmod 700 secrets` /
   `chmod 600 secrets/*.json`.

Keys: jenkins `username`,`token`; github `token`; argocd `token`;
grafana `token`; prometheus/loki/tempo `token` or `username`+`password`
(all optional — anonymous access works); db `password_<user>` per user
(optional; without it the CLI's own `.pgpass`/`.my.cnf` is used).

## Setting environment variables

- bash/zsh (`~/.zshrc`, `~/.bashrc`): `export OBSERVE_LENS_PIPELINES_CI_MAIN_TOKEN='...'`
- fish (`~/.config/fish/config.fish`): `set -x OBSERVE_LENS_PIPELINES_CI_MAIN_TOKEN ...`
- PowerShell (`$PROFILE`): `$env:OBSERVE_LENS_PIPELINES_CI_MAIN_TOKEN = '...'`
- Windows persistent: `setx OBSERVE_LENS_PIPELINES_CI_MAIN_TOKEN "..."`
  (opens in *new* terminals only)

Picking an instance without `--instance`:
`OBSERVE_LENS_PIPELINES_INSTANCE=ci-main` (same pattern per area).

## Knowledge notes

`knowledge/` holds Markdown *you* write about your systems: job names,
schemas, label names, app names, known ids, known flakes. The area skills
read these files before guessing names — they are how the generic skills
learn your organisation. `config init` drops a template. Write whatever
helps; short and specific beats long.

## Security

- Never commit `secrets/`, `.env` or tokens; the repo `.gitignore` covers
  the common names for your own checkouts.
- The scripts never print secret values (`config list` shows the *source*,
  not the value) and never put them on a command line.
- Prefer read-only API tokens and read-only database users; the guards are
  seatbelts, not permission systems.
