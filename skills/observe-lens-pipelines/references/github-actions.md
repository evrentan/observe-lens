# GitHub Actions notes

## Object model

workflow (`.github/workflows/*.yml`) → run (one execution; `id` is a large
number, `run_attempt` counts re-runs) → job (each has its own `id` and log)
→ step (`conclusion`: success/failure/skipped/cancelled).

- `runs <workflow>` takes the file name (`ci.yml`), workflow id, or `all`.
- `run`/`log`/`artifact` accept a full run URL:
  `https://github.com/acme/shop/actions/runs/12345[/job/678]` — the repo is
  taken from the URL, the job id selects the log job (`-` means ignore).
- Re-runs share the run id; use `--attempt N` for an earlier/later attempt.
  `conclusion` is only set when `status` is `completed`.

## Conclusions

`success`, `failure`, `cancelled`, `skipped`, `timed_out`,
`action_required`, `neutral`, `startup_failure`. A cancelled run usually
means concurrency cancellation or a user abort — check `event` and the
run's `display_title`/head commit before blaming code.

## Logs and artifacts quirks

- Job logs come from `/actions/jobs/{job_id}/logs` (a redirect); lines carry
  `2026-01-15T09:41:07.123Z ` timestamps that `log` strips unless
  `--keep-timestamps`.
- `log` defaults to **failed jobs only**; `--job <name|id>` picks one.
- Artifacts download as **zip** even when the artifact itself was a zip.
  `expired` artifacts cannot be fetched.
- `params <workflow>` prints the `workflow_dispatch` inputs block extracted
  from the workflow file (best effort).

## Token scopes

Fine-grained PAT: Actions: read, Contents: read, Metadata: read on the
repo(s). Classic PAT: `repo` for private repos, `public_repo` otherwise.
Fine-grained tokens cannot call `/user` — `whoami` falls back to reading
the configured repo to prove the token works.
