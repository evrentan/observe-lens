# Jenkins notes

## Job paths

Folders nest as `FOLDER/SUBFOLDER/JOB`, which the API spells
`job/FOLDER/job/SUBFOLDER/job/JOB`. `pipelines.py` accepts all of:

- `FOLDER/JOB` (converted automatically)
- `job/FOLDER/job/JOB` (kept as-is)
- a full build URL — `https://jenkins.example.com/view/All/job/team/job/api/812/`
  — the base and `/view/<x>/` prefix are stripped and the trailing number is
  the build.

## Useful API fields

`<job>/api/json` → `builds[]` (number, url), `lastCompletedBuild`,
`lastStableBuild`, `property` (parameter definitions).
`<build>/api/json` → `result` (SUCCESS/FAILURE/UNSTABLE/ABORTED/null while
building), `building`, `timestamp` (ms), `duration` (ms), `actions[]`
(causes, parameters, `lastBuiltRevision.SHA1` /
`buildsByBranchName.*.revision.SHA1`), `changeSets[].items[].commitId`,
`artifacts[]` (relativePath, fileName).

Causes worth quoting: `shortDescription` ("Started by user X", "Started by
upstream project Y #N", "Started by an SCM change") plus `upstreamProject`
/`upstreamBuild` for chained jobs.

## Pipeline quirks

- `UNSTABLE` usually means test failures in a pipeline; `FAILURE` means the
  pipeline itself failed. `ABORTED` is a user abort or a timeout step.
- Stage names and step failures appear in `consoleText`, not the JSON;
  search the saved log for `FAILED in stage` / `Error when executing`.
- `consoleText` can be megabytes — the `log` subcommand saves it to the temp
  folder; summarise with `log_summary.py`, don't print it.

## Common failure patterns

- `ERROR: script returned exit code N` → look one screen up for the real
  error.
- `hudson.remoting.*` / `ChannelClosedException` → agent died; infra, not
  the change.
- `Authentication failed` on SCM checkout → credentials issue on the
  controller/agent.
- `workspace` / `No space left` → agent disk; cleanup needed.
- Parameterized builds without parameters recorded → parameters come from
  the trigger (upstream or timer); check `causes`.
