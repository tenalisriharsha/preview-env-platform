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
- **Least privilege**: the workflows and CronJob use a Role scoped to namespaces
  matching the `preview-*` prefix, not cluster-admin.

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

### Phase 2 — Deploy/teardown automation (Night 2)

- [ ] Sample app + `k8s/base` Kustomize base (Deployment, Service, Ingress)
- [ ] `.github/workflows/preview.yaml` — build image, render overlay, apply, comment
- [ ] `.github/workflows/teardown.yaml` — delete namespace on PR close
- [ ] RBAC manifests (ServiceAccount + Role scoped to `preview-*`)
- [ ] End-to-end dry run against a local `kind` cluster (documented)

### Phase 3 — Cleanup CronJob + polish (Night 3)

- [ ] `k8s/cleanup` CronJob manifest running `previewctl cleanup --ttl 24h`
- [ ] `docs/` — architecture deep-dive, setup guide, screenshots/demo
- [ ] README final polish (badges, quickstart, demo GIF)
- [ ] Tag `v0.1.0`

## Resume point for Night 2

Phase 1 is complete and all tests pass (`pytest`: 66 tests green). Next:

1. Create `k8s/base/` (Deployment, Service, Ingress, kustomization.yaml) for a tiny
   sample app (plain nginx or a 20-line Python HTTP server with its own Dockerfile).
2. Write `.github/workflows/preview.yaml` and `teardown.yaml` following the
   architecture diagram above; wire them to `previewctl render/comment/teardown`.
3. Add `k8s/rbac.yaml` scoped to `preview-*` namespaces.
4. Validate locally with `kind`: `kind create cluster`, run the render + apply path
   by hand, record the commands in `docs/local-e2e.md`.
