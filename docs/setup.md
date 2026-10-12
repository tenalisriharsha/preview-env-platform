# Setup guide

How to wire preview-env-platform to a real cluster and a GitHub repository:
prerequisites, the credentials the workflows need, and the first deploy.
For a purely local run, see [local-e2e.md](local-e2e.md).

Each shell command below was run against a Kubernetes v1.34 cluster (k3s)
while writing this guide. Steps done in the GitHub web UI (secrets,
variables, package visibility) could not be exercised from that
environment and are marked as such.

## Prerequisites

| What | Why |
|---|---|
| Kubernetes **1.30+** | `k8s/cleanup/admission-policy.yaml` uses `ValidatingAdmissionPolicy` (GA in 1.30). Everything else works on older versions. |
| An ingress controller that serves Ingresses without an `ingressClassName` (i.e. a default IngressClass) | The base Ingress has no class set. Without one, previews deploy and are reachable via `kubectl port-forward`, but the PR URL will not resolve. |
| Wildcard DNS `*.<your preview domain>` pointing at that ingress controller | Each PR gets the host `pr-<N>.<domain>`. |
| An API server reachable from GitHub-hosted runners | The workflows run `kubectl` from `ubuntu-latest`. A laptop cluster (kind, k3s on `127.0.0.1`) is not reachable from there. |
| `kubectl`, Python 3.11+ and `pip install -e .` locally | For the commands in this guide. |
| Docker (or another OCI builder) | Only if you build the cleanup image yourself. |

## 1. Install the deployer RBAC

The workflows authenticate as the `preview-deployer` ServiceAccount, whose
ClusterRole covers namespaces, deployments, services and ingresses and
nothing else (no secrets, RBAC or nodes):

```bash
kubectl apply -f k8s/rbac.yaml
```

## 2. Build a kubeconfig for that ServiceAccount

Run this with an admin context selected. It writes a kubeconfig that
carries only the `preview-deployer` token:

```bash
SERVER=$(kubectl config view --minify -o jsonpath="{.clusters[0].cluster.server}")
kubectl config view --minify --raw \
  -o jsonpath="{.clusters[0].cluster.certificate-authority-data}" | base64 -d > ca.crt
TOKEN=$(kubectl -n default create token preview-deployer --duration=720h)

kubectl --kubeconfig=preview-deployer.kubeconfig config set-cluster preview \
  --server="$SERVER" --certificate-authority=ca.crt --embed-certs=true
kubectl --kubeconfig=preview-deployer.kubeconfig config set-credentials preview-deployer \
  --token="$TOKEN"
kubectl --kubeconfig=preview-deployer.kubeconfig config set-context preview \
  --cluster=preview --user=preview-deployer
kubectl --kubeconfig=preview-deployer.kubeconfig config use-context preview
```

Check it before you upload it:

```bash
kubectl --kubeconfig=preview-deployer.kubeconfig auth can-i create namespaces   # yes
kubectl --kubeconfig=preview-deployer.kubeconfig auth can-i get secrets -A      # no
```

Notes:

- `SERVER` must be an address GitHub's runners can reach. If your admin
  context points at `127.0.0.1` or a private IP, replace it with the
  public API endpoint.
- `kubectl create token` issues a time-limited token, and the API server
  may issue a shorter one than `--duration` asks for
  (`--service-account-max-token-expiration`). Plan to re-run this step and
  update the secret before it expires. Delete `ca.crt` and the kubeconfig
  once uploaded.

## 3. Repository secrets and variables

*(GitHub web UI; not exercised while writing this guide.)*

In **Settings → Secrets and variables → Actions**:

| Name | Kind | Value |
|---|---|---|
| `KUBECONFIG` | Secret | The full contents of `preview-deployer.kubeconfig` from step 2. Both workflows write it to `~/.kube/config`. |
| `PREVIEW_DOMAIN` | Variable (optional) | Your preview domain, e.g. `preview.example.org`. Defaults to `preview.example.com`. |

`GITHUB_TOKEN` is **not** something you create. GitHub injects it into
every workflow run; `preview.yaml` uses it to push the PR image to GHCR
(`packages: write`) and for `previewctl comment` to upsert the PR comment
(`pull-requests: write`). If your organisation restricts the default
token permissions, the `permissions:` block in the workflow must still be
allowed.

To run `previewctl comment` by hand you need your own token with write
access to the repository's pull requests:

```bash
export GITHUB_TOKEN=<a personal access token>
previewctl comment --repo <owner>/<repo> --pr <N> --sha <commit sha>
```

Without it the command stops with `error: GITHUB_TOKEN is not set`
(exit 2) before calling the API.

## 4. Let the cluster pull the PR images

`preview.yaml` pushes `ghcr.io/<owner>/<repo>/preview-app:pr-<N>`. New
GHCR packages are **private** by default, and nothing in this repository
gives the cluster a pull secret, so either:

- make the `preview-app` package public (package settings on GitHub), or
- add an `imagePullSecrets` entry to `k8s/base/deployment.yaml` and create
  that secret in each preview namespace yourself.

Until one of those is done the first preview Deployment will sit in
`ImagePullBackOff`. The same applies to the `previewctl` image used by the
cleanup CronJob (step 5).

## 5. Install the cleanup CronJob

`k8s/cleanup/` runs `previewctl cleanup --ttl 24h` every hour, in its own
`previewctl-system` namespace, as a ServiceAccount that may only
get/list/delete namespaces (and an admission policy narrows that to
`preview-*` namespaces carrying `preview.env/platform=true`).

It needs the image built from `Dockerfile.previewctl`. The kustomization
points at `ghcr.io/tenalisriharsha/preview-env-platform/previewctl:0.1.0`,
which `.github/workflows/release-image.yaml` publishes when the `v0.1.0`
tag is pushed. To use your own registry instead, build and push it:

```bash
docker build -f Dockerfile.previewctl -t <registry>/previewctl:0.1.0 .
docker push <registry>/previewctl:0.1.0
```

and point an overlay at it:

```bash
mkdir -p rendered/cleanup
cat > rendered/cleanup/kustomization.yaml <<'EOF'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../k8s/cleanup
images:
  - name: ghcr.io/tenalisriharsha/preview-env-platform/previewctl
    newName: <registry>/previewctl
    newTag: "0.1.0"
EOF
kubectl apply -k rendered/cleanup
```

If you use the published image as is, `kubectl apply -k k8s/cleanup` is
enough. Then check it and trigger one run without waiting for the hour:

```bash
kubectl -n previewctl-system get cronjob preview-cleanup
kubectl -n previewctl-system create job cleanup-now --from=cronjob/preview-cleanup
kubectl -n previewctl-system wait --for=condition=complete job/cleanup-now --timeout=120s
kubectl -n previewctl-system logs job/cleanup-now
kubectl -n previewctl-system delete job cleanup-now
```

The log line is `nothing to clean up` or one `deleted preview-...` line
per expired namespace. To change the TTL or schedule, patch `args` or
`schedule` of the CronJob in your overlay.

## 6. First deploy

Open a pull request (or push to an open one). `preview.yaml` then:

1. builds and pushes `preview-app:pr-<N>`,
2. runs `previewctl render` and `kubectl apply -k rendered/pr-<N>`,
3. waits for `deployment/app` to roll out,
4. comments the URL `https://pr-<N>.<domain>` on the PR.

Closing or merging the PR runs `teardown.yaml`, which deletes the
namespace. Check from your admin context:

```bash
kubectl get namespaces -l preview.env/platform=true -L preview.env/pr
```

Pull requests from **forks** do not get repository secrets or a writable
`GITHUB_TOKEN`, so previews only work for branches in the repository
itself.
