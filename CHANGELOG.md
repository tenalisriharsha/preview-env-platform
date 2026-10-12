# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

First release: per-PR preview environments on Kubernetes, driven by GitHub
Actions, with an in-cluster safety net.

### Added

- `previewctl`, a stdlib-only Python CLI:
  - `name`: DNS-1123-safe namespace `preview-<repo>-pr-<N>` for a PR.
  - `render`: per-PR Kustomize overlay (namespace, image retag, platform
    labels, ingress host) plus the labelled `namespace.yaml` it applies.
  - `comment`: one preview-URL comment per PR, found by an HTML marker and
    updated in place.
  - `teardown`: deletes the PR's namespace; refuses anything without the
    `preview-` prefix; idempotent.
  - `cleanup`: deletes labelled preview namespaces older than a TTL
    (`--ttl 24h`, `--dry-run`). One failing namespace no longer stops the
    sweep; the run exits 1 if any delete failed.
- Sample app (`app/`) and Kustomize base (`k8s/base/`).
- Deployer RBAC (`k8s/rbac.yaml`) for the workflows.
- Workflows: CI (tests and a build of the cleanup image), `preview.yaml`,
  `teardown.yaml`, and `release-image.yaml` (publishes the cleanup image
  on `v*` tags).
- `k8s/cleanup/`: hourly CronJob running `previewctl cleanup --ttl 24h`,
  its own least-privilege ServiceAccount/ClusterRole/ClusterRoleBinding
  (get/list/delete on namespaces only), a `ValidatingAdmissionPolicy`
  restricting that identity to deleting `preview-*` namespaces labelled
  `preview.env/platform=true`, and a Pod Security `restricted` namespace.
- `Dockerfile.previewctl`: previewctl plus a checksum-pinned kubectl,
  non-root.
- Docs: architecture deep-dive, setup guide, local end-to-end guide, and
  README screenshots generated from real command output.

### Known limitations

See [docs/architecture.md](docs/architecture.md#known-limitations): the
TTL counts from first deploy rather than last activity, namespace names
ignore the repository owner, the deployer identity has no admission guard,
fork PRs get no preview, and PR comment lookup reads one page.

[0.1.0]: https://github.com/tenalisriharsha/preview-env-platform/releases/tag/v0.1.0
