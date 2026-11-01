# Kubernetes, Flux and Helm notes

All access goes through `kube_ro.py`, which only runs read verbs and
refuses secrets, watch/follow and state-changing commands.

## Kubernetes

- `kubectl get pods -n <ns> -l <selector>` → pod phase and `RESTARTS`.
  High restart counts + `CrashLoopBackOff`/`ImagePullBackOff`/`OOMKilled`
  explain a Degraded app.
- `kubectl describe pod <pod> -n <ns>` → Events section has probe failures,
  pull errors and OOM kills.
- `kubectl logs <pod> [-c <container>] -n <ns> [--previous]` → the crash
  reason (previous is the last crashed container).
- `kubectl get events -n <ns> --sort-by=.lastTimestamp` → the cluster's own
  narrative: scheduling failures, volume issues, kills.
- `kubectl rollout status|history deploy/<name> -n <ns>` → is the rollout
  done? which revision is live?
- `kubectl top pods -n <ns>` (metrics-server) → CPU/memory pressure.
- `kubectl auth can-i <verb> <resource>` → check permissions without trying
  a write.
- Always pass `-n` and, with several clusters, `--context` — or use
  `--instance <name>` so kube_ro injects them from the instance config.

## Flux

- `flux get kustomizations -A` / `flux get helmreleases -A` → READY status
  and the last applied revision per object.
- `flux tree kustomization <name>` → which resources a kustomization owns.
- `flux events -n flux-system` → reconcile errors (fetch failures, apply
  errors) land here.
- `flux logs` → source-controller/kustomize-controller errors.
- A `False` READY usually has the reason in `.status.conditions` — see it
  with `kubectl get kustomization <name> -n <ns> -o jsonpath=...` or
  `kubectl describe`.

## Helm

- `helm list -A` → releases, namespaces, chart and app versions, last
  updated time.
- `helm history <release> -n <ns>` → revisions with status
  (deployed/superseded/failed) — a `pending-upgrade` or `failed` newest
  revision means a broken upgrade.
- `helm status <release> -n <ns>` → release state and hook results.
- `kube_ro.py` deliberately refuses `helm get values|manifest|all` —
  rendered manifests and values frequently contain credentials.
