# Security

observe-lens is read-only by design: its scripts only send GET requests and
refuse write statements. Even so, it handles credentials and may read
production data.

- Never commit secrets: `secrets/` config folders, tokens, `.env`,
  kubeconfig credentials. See `.gitignore`.
- Report vulnerabilities with GitHub private vulnerability reporting:
  https://github.com/evrentan/observe-lens/security/advisories/new
  Please do not open public issues for vulnerabilities.
- Supported versions: the latest release.
- Prefer read-only API tokens and database users; the guards in the scripts
  are defense in depth, not a permission system.
