# Correlating evidence across areas

## Keys each area exposes

| Area | Keys you can join on |
| --- | --- |
| pipelines | commit SHA, branch, run id/number, image tag (in artifacts/params), run start/end time, trigger cause |
| cd | git revision (of the *manifest* repo), image tags (`status.summary.images`), sync start/finish times, app, namespace, history id |
| telemetry | trace id, request/correlation id (in log fields and span attributes), service/pod labels, timestamps, alert windows |
| db | record ids (order/customer/request), status columns, created/updated timestamps |

## Building the timeline

1. Anchor on the user's evidence: a run URL gives a commit and a time; a
   trace id gives a service and a time; a record id gives both from the row.
2. Convert every timestamp to UTC before comparing (scripts already print
   UTC; Jenkins `timestamp` is ms, GitHub ISO strings, Loki ns).
3. Order events: commit → build → artifact/tag → manifest bump → sync →
   Healthy → runtime evidence → data state.
4. A commit reaches a cluster as: app commit → CI builds image tag X →
   config repo commit sets `image: X` → Argo CD syncs that config revision.
   The Argo CD revision is usually **not** the app commit — compare image
   tags, and check the user's knowledge notes for the mapping.

## Traps

- **Time zones**: paste times back in UTC; a ±1h error puts log searches in
  the wrong window silently.
- **Deploy overlap**: a test run that started before the new version was
  Healthy tested a mixed/old environment. Compare run start to sync finish,
  not sync start.
- **Retention**: Loki/Tempo retention and CI log rotation mean "no results"
  is not "did not happen" — state the window and retention caveat.
- **Sampling**: traced requests are often sampled; a missing trace is not a
  missing request. Logs usually survive longer.
- **Clock skew**: a few seconds between systems is normal; don't read order
  into events closer than ~5s.
- **Retried CI attempts**: GitHub `run_attempt` and Jenkins rebuilds share
  the run id but not the result — check which attempt you looked at.
- **Same failure, different run**: compare with the previous run of the
  same job/environment to separate a new failure from a known one.
- **Argo revision vs app commit**: the deployed revision is the manifest
  repo's; map via image tags before claiming "commit X is live".
