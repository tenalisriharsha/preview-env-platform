# Progress — preview-env-platform

STATUS: PHASE_3_COMPLETE (v0.1.0 tag pending)

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
  `preview-*` boundary is enforced by `previewctl` (see `k8s/rbac.yaml`);
  the cleanup CronJob's identity is additionally fenced by a
  ValidatingAdmissionPolicy (`k8s/cleanup/admission-policy.yaml`).

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

### Phase 3 — Cleanup CronJob + polish ✅ (Night 3; tag pending)

- [x] `k8s/cleanup` CronJob running `previewctl cleanup --ttl 24h` hourly,
      with its own ServiceAccount/ClusterRole/ClusterRoleBinding
      (get/list/delete on namespaces only), a ValidatingAdmissionPolicy
      limiting deletes to labelled `preview-*` namespaces, and
      `Dockerfile.previewctl` for its image (built in CI, published on
      `v*` tags by `release-image.yaml`)
- [x] `previewctl cleanup` keeps going when one namespace fails to delete
- [x] `docs/architecture.md`, `docs/setup.md`, cleanup section in
      `docs/local-e2e.md`, cleanup screenshots (11-16 from a live cluster)
- [x] README: CI badge, quickstart, honest verified / not-verified list
- [ ] Demo GIF: not made; the screenshot set covers the flow instead
- [ ] Tag `v0.1.0`: CHANGELOG entry prepared; the tag itself is left to
      the maintainer

## Verification status

Verified on a live single-node k3s v1.34 cluster (by hand, not in CI):
preview deploy with `kubectl apply -k`, pod Ready, `curl` through the
Service and via `kubectl port-forward`; deploy + teardown using only the
`preview-deployer` kubeconfig; `previewctl cleanup` deleting only the
expired of two previews; the CronJob running in-cluster on demand and on
schedule under Pod Security `restricted` and deleting an expired
namespace; RBAC `can-i` checks and admission-policy denials for the
cleanup ServiceAccount; every shell command in `docs/setup.md` and
`docs/local-e2e.md` (kind commands excepted).

Not verified: `kind` itself (could not run nested containers in the
build environment), the GitHub Actions preview/teardown workflows against
a real cluster, Ingress routing to `pr-<N>.<domain>`, and the
`release-image.yaml` publish (needs a tag push).

## Next

1. Tag `v0.1.0` (see CHANGELOG.md) and confirm `release-image.yaml`
   publishes `ghcr.io/tenalisriharsha/preview-env-platform/previewctl:0.1.0`;
   make the package public or add a pull secret.
2. Run the preview/teardown workflows against a reachable cluster.
3. Address the known limitations in `docs/architecture.md`, starting with
   the TTL counting from first deploy rather than last activity.
