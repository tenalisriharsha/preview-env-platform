# preview-env-platform

A mini platform-engineering tool: **every pull request gets its own temporary
Kubernetes environment** — a namespace running the PR build of the app, with the
preview URL commented back on the PR. Merged or closed? The environment is torn
down automatically. Forgotten? A cleanup CronJob sweeps it up.

GitHub Actions driven · Kustomize based · stdlib-only Python CLI · fully tested.

## How it works

```
PR opened/updated ──► GitHub Actions ──► build image (PR tag)
                                     ──► render Kustomize overlay (previewctl)
                                     ──► kubectl apply → namespace preview-*-pr-N
                                     ──► comment preview URL on the PR

PR closed/merged  ──► GitHub Actions ──► kubectl delete namespace

Hourly (safety)   ──► in-cluster CronJob ──► delete expired preview namespaces
```

## Project Status

**In active development** — built in public, one phase per night. See
[PROGRESS.md](PROGRESS.md) for the full architecture, the phased build plan, and
the current status.

- Phase 1 — core `previewctl` CLI (naming, overlay rendering, PR comments, TTL cleanup) ✅
- Phase 2 — Kustomize base + deploy/teardown GitHub Actions workflows
- Phase 3 — cleanup CronJob, docs, demo, `v0.1.0`

## Layout

```
src/previewctl/   stdlib-only CLI: naming, render, comment, cleanup, kubectl/GitHub I/O
tests/            pytest suite for everything above
k8s/              Kustomize base + overlays + RBAC + cleanup CronJob   (Phase 2–3)
.github/          CI (tests) now; preview/teardown workflows in Phase 2
```

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
pytest
```

## License

MIT
