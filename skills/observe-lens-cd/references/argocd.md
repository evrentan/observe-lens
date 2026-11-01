# Argo CD notes

## Sync vs health

- **Sync** (`status.sync.status`): does the cluster state match the desired
  manifests? `Synced` / `OutOfSync` / `Unknown`.
- **Health** (`status.health.status`): are the resources actually working?
  `Healthy` / `Progressing` / `Degraded` / `Suspended` / `Missing`.
- An app can be Synced and Degraded at once (deployed fine, pods failing).

## Revision semantics

`status.sync.revision` is the commit of the **manifest/config repo**
(spec.source.repoURL), not the application repo. For multi-source apps
(`spec.sources[]`) there is `revisions[]`. To answer "is commit X live?",
compare `status.summary.images` tags, or know from the user's notes which
repo feeds the app.

## Operation state

`status.operationState` describes the latest sync: `phase`
(Running/Succeeded/Failed/Error), `startedAt`, `finishedAt`, `message`
(often a kubectl error for failures). "Since when is it healthy?" ≈
finishedAt of the successful operation plus the time health flipped —
`history` shows `deployedAt` per sync.

## History

`status.history[]` newest last: `id`, `revision`, `deployStartedAt`,
`deployedAt`, `source`. Rollback ("History and Rollback" in the UI) syncs
an old history entry — it is a *user* action, never done by the skill.

## Resources and events

`cd.py resources <app> --unhealthy` lists resource-tree nodes whose health
is not Healthy — names the Deployment/Pod to dig at with kube_ro.py.
`cd.py events <app>` shows the app's Kubernetes events. `conditions[]`
carries warnings like shared-resource and sync errors.

## App-of-apps

An app that only deploys other `Application` resources: drill into the
child apps with `cd.py apps`/`app`. Sync waves and `sync-wave` annotations
explain ordering when children deploy in stages.

## Useful API paths (`cd.py get <path>`)

`/api/v1/applications`, `/api/v1/applications/{name}`,
`/api/v1/applications/{name}/resource-tree`,
`/api/v1/applications/{name}/events`,
`/api/v1/session/userinfo`.
