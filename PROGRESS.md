# Progress — preview-env-platform

STATUS: IN_PROGRESS

## Vision

A mini platform-engineering tool: for every pull request, automatically spin up a
temporary Kubernetes namespace running the PR version of an app, comment the preview
URL on the PR via the GitHub API, and tear the environment down when the PR is merged
or closed. GitHub Actions driven, Kustomize based, with a cleanup CronJob as a safety
net — and full documentation.

Built in public, one phase per night.

## Architecture

```
Pull Request opened/synchronized
        │
        ▼
┌─────────────────────────────┐
│ GitHub Actions: preview.yaml │
│  1. build & push image (PR tag)        │
│  2. previewctl render  → kustomize overlay for this PR │
│  3. kubectl apply -k overlay → namespace preview-<repo>-pr-<N> │
│  4. previewctl comment → upsert preview URL on the PR │
└─────────────────────────────┘
        │
        ▼
Kubernetes cluster (any: kind, minikube, EKS, GKE)
  namespace preview-<repo>-pr-<N>
    └─ app Deployment + Service + Ingress (host pr-<N>.preview.example.com)
        labels: preview.env/platform=true, preview.env/pr=<N>

Pull Request closed/merged
        │
        ▼
┌──────────────────────────────┐
│ GitHub Actions: teardown.yaml │
│  previewctl teardown → kubectl delete namespace │
└──────────────────────────────┘

Safety net (in-cluster):
┌──────────────────────────────┐
│ CronJob: preview-cleanup      │
│  hourly: previewctl cleanup --ttl 24h │
│  deletes orphaned/expired preview namespaces │
└──────────────────────────────┘
```

Design principles:

- **Stdlib-only CLI** (`previewctl`, Python): no runtime dependencies, so it runs
  anywhere Actions runs. Pure, unit-testable logic (naming, TTL math, comment
  templating) separated from thin I/O shells (kubectl subprocess, GitHub REST).
- **Kustomize, not templating**: one `base` for the app; `previewctl render`
  generates a per-PR overlay (namespace, image tag, ingress host).
- **Marker-based comment upsert**: the bot finds its own previous comment via an
  HTML marker (`<!-- preview-env -->`) and updates it — one comment per PR, no spam.
- **Label-driven lifecycle**: every preview namespace carries
  `preview.env/*` labels, so teardown and the cleanup CronJob never touch
  anything else.
- **Least privilege**: the workflows use a dedicated ClusterRole limited to
  namespaces, deployments, services and ingresses (no secrets, RBAC or nodes),
  not cluster-admin. RBAC cannot match namespace names by prefix, so the
  `preview-*` boundary is enforced by `previewctl` (see `k8s/rbac.yaml`).

## Build plan

### Phase 1 — Scaffold + core foundation ✅ (Night 1)

- [x] Repo scaffold: `pyproject.toml`, `.gitignore`, src layout, venv, pytest
- [x] `previewctl.naming` — DNS-1123-safe namespace name from repo + PR number
- [x] `previewctl.render` — per-PR Kustomize overlay generation
- [x] `previewctl.comment` — marker-based PR comment body + GitHub API upsert client
- [x] `previewctl.cleanup` — TTL-based expiry decision logic
- [x] `previewctl` CLI wiring (`name`, `render`, `comment`, `teardown`, `cleanup`)
- [x] Unit tests for all of the above (pytest, GitHub API mocked)
- [x] CI workflow running the test suite

### Phase 2 — Deploy/teardown automation ✅ (Night 2)

- [x] Sample app + `k8s/base` Kustomize base (Deployment, Service, Ingress)
- [x] `.github/workflows/preview.yaml` — build image, render overlay, apply, comment
- [x] `.github/workflows/teardown.yaml` — delete namespace on PR close
- [x] RBAC manifests (ServiceAccount + ClusterRole; prefix boundary enforced
      by previewctl, since RBAC cannot match namespace-name patterns)
- [x] Local dry run documented in `docs/local-e2e.md` (kind-based); clusterless
      validation automated via `kubectl kustomize` in `tests/test_manifests.py`

### Phase 3 — Cleanup CronJob + polish (Night 3)

- [ ] `k8s/cleanup` CronJob manifest running `previewctl cleanup --ttl 24h`
- [ ] `docs/` — architecture deep-dive, setup guide, screenshots/demo
- [ ] README final polish (badges, quickstart, demo GIF)
- [ ] Tag `v0.1.0`

## Resume point for Night 3

Phase 2 is complete and all tests pass (`pytest`: 76 tests green). What landed:

- `app/` — stdlib HTTP server + Dockerfile (verified locally with curl).
- `k8s/base/` + `k8s/rbac.yaml`; `.github/workflows/preview.yaml` and
  `teardown.yaml` wired to `previewctl render/comment/teardown`.
- Bug fix: `previewctl render` now rewrites `--base` relative to the overlay
  directory (kustomize resolves it from there, not from the cwd).
- `docs/local-e2e.md` — kind dry-run walkthrough. Note: no docker daemon or
  kind on this machine, so the image build and live apply were NOT executed;
  clusterless validation (`kubectl kustomize` on base + rendered overlay)
  passes and is covered by tests.

Next:

1. Add `k8s/cleanup/cronjob.yaml` (+ its own SA/ClusterRole limited to
   namespaces get/list/delete) running `previewctl cleanup --ttl 24h` hourly;
   it needs a container image with previewctl — build one from this repo or
   pip-install from git in an init step. Decide and document.
2. Write `docs/architecture.md` and `docs/setup.md` (secrets: KUBECONFIG,
   PREVIEW_DOMAIN var; installing RBAC; ingress controller expectations).
3. Final README polish (badges, quickstart). Demo GIF only if tooling allows.
4. Write DAILY_REPORT.md, tag `v0.1.0`, set STATUS: COMPLETE.
