# Architecture

preview-env-platform gives every pull request its own short-lived
Kubernetes namespace running the PR build of an app, comments the URL on
the PR, and removes the namespace when the PR closes, with an in-cluster
CronJob as a backstop for anything teardown misses.

## Components

| Component | Where | Role |
|---|---|---|
| `previewctl` | `src/previewctl/` | Stdlib-only Python CLI. Every workflow step and the CronJob go through it. |
| Sample app | `app/` | Tiny HTTP server (`/` and `/healthz`), the workload each preview runs. |
| Kustomize base | `k8s/base/` | Deployment, Service and Ingress for the app. |
| Deployer RBAC | `k8s/rbac.yaml` | `preview-deployer` ServiceAccount used by the workflows. |
| Preview workflow | `.github/workflows/preview.yaml` | PR opened/synchronized/reopened: build, render, apply, comment. |
| Teardown workflow | `.github/workflows/teardown.yaml` | PR closed: delete the namespace. |
| Cleanup CronJob | `k8s/cleanup/` | Hourly `previewctl cleanup --ttl 24h` inside the cluster. |
| Cleanup image | `Dockerfile.previewctl` | previewctl plus a checksum-pinned `kubectl`; published by `.github/workflows/release-image.yaml` on `v*` tags. |

### Inside previewctl

The CLI keeps decisions pure and I/O thin so most of it is unit-tested
without a cluster or network:

| Module | Pure / I/O | What it does |
|---|---|---|
| `naming.py` | pure | Namespace name for a repo + PR number. |
| `render.py` | pure (+ file write) | Per-PR Kustomize overlay and its `namespace.yaml`. |
| `comment.py` | template pure; client does HTTP | Comment body; GitHub REST upsert via `urllib`. |
| `cleanup.py` | pure | TTL parsing, expiry decision, parsing `kubectl get namespaces -o json`. |
| `kube.py` | I/O | The only module that runs `kubectl` (subprocess). |
| `cli.py` | glue | Subcommands `name`, `render`, `comment`, `teardown`, `cleanup`. |

## Data flow

```
PR opened / pushed
  └─ preview.yaml
       1. docker build app/ → push ghcr.io/<owner>/<repo>/preview-app:pr-<N>
       2. previewctl render → rendered/pr-<N>/{kustomization,namespace}.yaml
       3. kubectl apply -k rendered/pr-<N>          (creates the namespace too)
       4. kubectl rollout status deployment/app
       5. previewctl comment → create or update the one marked PR comment

PR closed / merged
  └─ teardown.yaml
       previewctl teardown → kubectl delete namespace preview-<repo>-pr-<N>

Every hour, in the cluster
  └─ CronJob previewctl-system/preview-cleanup
       previewctl cleanup --ttl 24h
         kubectl get namespaces -l preview.env/platform=true -o json
         for each one older than 24h: kubectl delete namespace <name>
```

### The rendered overlay

`previewctl render` writes an overlay that points back at `k8s/base` and:

- sets `namespace: preview-<repo>-pr-<N>` on every object and includes a
  `namespace.yaml`, so `kubectl apply -k` creates the namespace itself;
- retags the image: `--image` must be the base name (`preview-app`) and
  `--new-name` the registry path, because kustomize only rewrites images
  whose name matches the base;
- adds the labels `preview.env/platform: "true"` and `preview.env/pr: "<N>"`
  to every object, the Namespace included;
- patches the Ingress host to `pr-<N>.<domain>`.

`--base` is given relative to the current directory and rewritten to be
relative to the overlay directory, which is how kustomize resolves it.

### The PR comment

`previewctl comment` lists the PR's comments, finds the one containing the
marker `<!-- preview-env -->`, and edits it; only when none exists does it
create one. Each push therefore updates a single comment instead of adding
another.

## Naming

`naming.namespace_for_pr(repo, pr)` returns `preview-<repo name>-pr-<N>`:

- only the repository name is used, not the owner (`octocat/demo` →
  `preview-demo-pr-7`);
- the name is lowercased and anything outside `[a-z0-9-]` collapses to a
  single `-`;
- the result is a valid DNS-1123 label (≤ 63 characters). Long repository
  names are truncated, never the `-pr-<N>` suffix.

The `preview-` prefix is load-bearing: `kube.delete_namespace` refuses any
name without it, and the cleanup admission policy checks it too.

## Lifecycle, TTL and the safety sweep

Teardown is the primary path: closing a PR deletes its namespace
(`--wait=false --ignore-not-found`, so re-running it is harmless). It can
still be missed: the workflow can fail, be cancelled, lose cluster access,
or never fire (repository deleted, workflow disabled). The CronJob is the
backstop.

How `previewctl cleanup` decides:

1. List namespaces with `preview.env/platform=true`. Namespaces without
   the label are never seen.
2. For each, compare `now - metadata.creationTimestamp` with the TTL
   (`30m`, `24h`, `7d`; units `s m h d`). Equal to or older than the TTL
   means expired.
3. Delete each expired namespace. A refusal (name without `preview-`) or
   an API error is printed for that namespace and the sweep carries on
   with the rest; the command then exits 1, so the Job is marked failed
   and shows up in `kubectl get jobs`.

`--dry-run` prints `would delete ...` and changes nothing.

The CronJob runs at minute 0 of every hour (UTC) with
`concurrencyPolicy: Forbid` (a slow sweep is never doubled up),
`startingDeadlineSeconds: 600`, `activeDeadlineSeconds: 600`,
`backoffLimit: 1` and three successful plus three failed Jobs kept for
inspection.

**What the TTL measures.** The age is taken from the namespace's
`creationTimestamp`, which the API server sets when the namespace is first
created and which cannot be changed afterwards. Re-applying the overlay on
a new push does not reset it. So a PR that stays open longer than the TTL
loses its environment at the next sweep, even if it is still being worked
on; the next push to that PR recreates it from scratch. This is a known
limitation (see "Known limitations" below).

## RBAC and the safety boundary

Kubernetes RBAC can grant verbs on a resource type, or on objects named
exactly; it cannot say "only namespaces whose name starts with `preview-`"
or "only namespaces with this label". Both identities are therefore as
narrow as RBAC allows, and the name/label boundary is enforced elsewhere.

### `preview-deployer` (the workflows)

`k8s/rbac.yaml`: get/list/watch/create/delete on namespaces, and
get/list/watch/create/update/patch/delete on deployments, services and
ingresses. No secrets, pods, RBAC, nodes or cluster configuration. The
`preview-` prefix check in `previewctl teardown` is the only thing keeping
its namespace deletes on preview namespaces.

### `preview-cleanup` (the CronJob)

`k8s/cleanup/rbac.yaml` grants exactly what `previewctl cleanup` calls:

| kubectl call | API verbs |
|---|---|
| `kubectl get namespaces -l preview.env/platform=true -o json` | `list` |
| `kubectl delete namespace <name> --wait=false --ignore-not-found` | `get`, `delete` |

so the ClusterRole is `get`, `list`, `delete` on `namespaces` and nothing
else. `tests/test_cleanup_manifests.py` runs a real `previewctl cleanup`
against a fake `kubectl` and fails if the role grants more or less than
those calls need.

On top of RBAC, `k8s/cleanup/admission-policy.yaml` is a
`ValidatingAdmissionPolicy` that matches namespace `DELETE` requests made by
`system:serviceaccount:previewctl-system:preview-cleanup` (and nobody
else) and denies them unless the target is named `preview-*` **and** has
`preview.env/platform=true`. A bug in previewctl, or someone using the
token directly, still cannot delete `kube-system` or an application
namespace.

The boundary for the cleanup identity, from the outside in:

1. RBAC: namespaces only, get/list/delete only.
2. Admission policy: deletes only for `preview-*` + platform label.
3. previewctl: lists by label, refuses names without `preview-`.

### The CronJob pod

- Namespace `previewctl-system` enforces Pod Security `restricted`; the
  name has no `preview-` prefix and no platform label, so cleanup and
  teardown can never select it.
- Runs as uid/gid 65532, `runAsNonRoot`, `RuntimeDefault` seccomp, no
  privilege escalation, all capabilities dropped, read-only root
  filesystem; `/tmp` is an `emptyDir` because the image sets `HOME=/tmp`
  for kubectl's cache.
- Requests 10m CPU / 32Mi memory, limited to 200m / 128Mi.
- `kubectl` in the pod uses the ServiceAccount token mounted by
  Kubernetes (in-cluster config); there is no kubeconfig in the image.

## Known limitations

- **TTL counts from first deploy, not last activity** (see above).
- **Namespace names ignore the repository owner.** Two repositories with
  the same name deploying into one cluster share namespaces.
- **The deployer can delete any namespace.** Its RBAC allows it; only
  previewctl's prefix check stops it. The cleanup identity has an
  admission policy for this, the deployer does not yet.
- **Fork pull requests** get no secrets and a read-only `GITHUB_TOKEN`, so
  they get no preview.
- **Private GHCR images** need a pull secret or a public package; nothing
  here sets one up (see [setup.md](setup.md#4-let-the-cluster-pull-the-pr-images)).
- **The Ingress has no `ingressClassName`** and relies on a default
  IngressClass.
- **Comment lookup reads one page.** `GitHubClient.list_comments` does not
  paginate, so it sees only the first page of PR comments (30 by default).
  If the bot's comment is not on it, a second comment is created.
