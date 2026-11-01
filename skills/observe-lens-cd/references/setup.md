# CD setup

## Config fields

| Provider | Required | Optional | Secret keys |
| --- | --- | --- | --- |
| argocd | `url` | `environment`, `timeout`, `ca_file` | `token` |
| kubernetes | `context` | `kubeconfig`, `namespace`, `environment` | — (uses your kubeconfig) |

## Argo CD token

Create a local account with the `apiKey` capability in `argocd-cm`:

```yaml
data:
  accounts.investigator: apiKey
```

Bind it to a read-only role in `argocd-rbac-cm`:

```csv
p, role:investigator-ro, applications, get, */*, allow
p, role:investigator-ro, applications, sync, */*, deny
g, investigator, role:investigator-ro
```

Then generate the token (the *user* runs this):

```
argocd account generate-token --account investigator
```

Add it with `cd.py config add argocd-prod --provider argocd --url https://argocd.example.com`.

## Kubernetes contexts

`kube_ro.py` runs your existing `kubectl`/`helm`/`flux` with your existing
kubeconfig. Put `context` (and optionally `kubeconfig`, `namespace`) in a
`kubernetes` provider instance and pass `--instance <name>` so the flags
are injected per command — the current context is never switched.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| HTTP 401 on argocd | token expired — regenerate with `argocd account generate-token` |
| HTTP 403 | the account's role lacks `applications get`; check rbac-cm |
| `kubectl/helm/flux not found` | see the install hint printed; `brew install kubectl helm`, distro packages, or `winget search` on Windows |
| `refused: ...` from kube_ro | that command is not a read — run it yourself if you must |
| wrong cluster answers | pass `--context`/`--instance`; several contexts is the usual cause |
| TLS error | set `ca_file` to the PEM bundle of the CA that signed the server certificate |
