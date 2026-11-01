# Contributing to observe-lens

Thanks for helping. Bug reports, new providers, better docs and new guard
test cases are all welcome.

## Ground rules

- **Read-only stays read-only.** Scripts may only send GET requests, run
  read statements and call read-only CLI subcommands. Anything that changes
  state belongs in the docs as a command for the user to run, never in a
  script.
- **Standard library only** in `skills/*/scripts/`. Test tooling
  (`requirements-dev.txt`) is the only place for third-party packages.
- **No real data.** Never commit hostnames, IPs, tokens, customer data,
  company names or logs from real systems. Use `example.com`, `*.internal`
  and `<token>` placeholders. Redact anything you paste into issues too.
- **Cross-platform.** Code must run on Linux, macOS and Windows with
  Python 3.11+.
- **Security issues** go through private reporting, not public issues. See
  [`SECURITY.md`](SECURITY.md).

## Development setup

```bash
git clone https://github.com/evrentan/observe-lens.git
cd observe-lens
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install --require-hashes -r requirements-dev.txt
```

## Making a change

- `common/_ol_common.py` is the single source of the shared helpers. Edit it
  there, then run `python tools/sync_common.py` to copy it into each skill.
  Never edit the `scripts/_ol_common.py` copies directly.
- Keep every function at cognitive complexity 15 or below.
- Changes to the SQL, MongoDB, kubectl/helm/flux or path guards need tests
  for both the allowed and the refused cases. If a guard's answer changes on
  purpose, regenerate the golden file with `python tools/make_guard_golden.py`
  and explain why in the pull request.
- If you change behaviour, update the matching `SKILL.md`, `references/` docs
  and the README.
- New CI or telemetry backends: follow `references/adding-a-provider.md` in
  `observe-lens-pipelines` or `observe-lens-telemetry`.

## Checks before a pull request

CI runs these on Linux, macOS and Windows. Run them locally first:

```bash
python tools/sync_common.py --check
python tools/validate_skills.py
python -m pytest
complexipy common/_ol_common.py skills/observe-lens-pipelines/scripts skills/observe-lens-cd/scripts skills/observe-lens-db/scripts skills/observe-lens-telemetry/scripts tools tests --max-complexity-allowed 15 --failed --plain
```

## Pull requests

- Keep each pull request focused on one change.
- Describe what changed and why, and how you tested it.
- Add an entry under `## [Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md).
- New dependencies, including GitHub Actions, must be pinned (actions to a
  full commit SHA, Python packages with hashes) and released at least 7 days
  earlier.

By contributing, you agree that your contributions are licensed under the
Apache License 2.0.
