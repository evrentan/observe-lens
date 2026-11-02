# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking:** scripts refuse production instances (environment `prod`,
  `production`, `prd` or `live`, or `"confirm": true`) unless the command
  includes `--prod`. The flag only allows the run; every read-only guard
  still applies. ([#1](https://github.com/evrentan/observe-lens/issues/1))

### Fixed

- `db.py columns` on PostgreSQL and MySQL no longer exits with an
  "unknown subcommand" error after printing the columns.

## [1.0.1] - 2026-11-01

### Added

- Project logo, used in the README and the Codex and Cursor plugin cards.

### Fixed

- Codex and Cursor installs: add the `.codex-plugin/plugin.json` and
  `.cursor-plugin/plugin.json` manifests those tools require.
- `config init` in a development checkout copies `config.example.json` again.

## [1.0.0] - 2026-10-31

### Added

- `observe-lens` router skill that picks the right area and correlates
  findings across CI, CD, databases and telemetry.
- `observe-lens-pipelines`: Jenkins and GitHub Actions runs, logs, test
  reports and artifacts.
- `observe-lens-cd`: Argo CD, Flux, Helm and Kubernetes through a read-only
  kubectl/helm/flux wrapper.
- `observe-lens-db`: PostgreSQL, MySQL and MongoDB read-only queries, with
  optional SSH tunnels.
- `observe-lens-telemetry`: Grafana, Loki, Tempo and Prometheus.
- Per-user configuration and knowledge notes under `~/.config/observe-lens/`.
- Read-only guards, secret masking, production confirmation and time-window
  limits.
- Plugin manifests for Devin, Claude Code, Codex and Cursor.

[Unreleased]: https://github.com/evrentan/observe-lens/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/evrentan/observe-lens/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/evrentan/observe-lens/releases/tag/v1.0.0
