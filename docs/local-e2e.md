# Local end-to-end dry run

How to exercise the whole deploy/teardown path by hand against a local
[kind](https://kind.sigs.k8s.io/) cluster — the same steps the GitHub Actions
workflows run, minus the PR comment.

Prerequisites: `kind`, `kubectl`, `docker`, and the package installed
(`pip install -e .`).

> **What has actually been run.** Steps 3-6 were run for real against a
> single-node **k3s v1.34** cluster on the Docker runtime. That runtime
> sees locally built images, so `kind load` was not needed. The run covered
> the pod becoming Ready, `curl` through the Service, and the cleanup
> CronJob deleting an expired namespace in-cluster. `kind create cluster`
> could not run in that environment (nested containers failed to start),
> so the `kind` commands below are the standard kind workflow and were
> not executed.

## 1. Create the cluster and install RBAC

```bash
kind create cluster --name preview-e2e
kubectl apply -f k8s/rbac.yaml
```

## 2. Build and load the app image

```bash
docker build -t preview-app:pr-7 app/
kind load docker-image preview-app:pr-7 --name preview-e2e
```

## 3. Render the overlay for a pretend PR

```bash
previewctl render \
  --repo octocat/demo --pr 7 \
  --image preview-app \
  --base ./k8s/base \
  --domain preview.localtest.me \
  --out rendered/pr-7
```

This prints the namespace (`preview-demo-pr-7`), the host
(`pr-7.preview.localtest.me`), and writes `rendered/pr-7/kustomization.yaml`
plus the `rendered/pr-7/namespace.yaml` it references (so `kubectl apply -k`
creates the labelled namespace itself).
The `--base` path is given relative to the current directory; `previewctl`
rewrites it so it resolves correctly from the overlay directory.

## 4. Inspect and apply

No cluster needed to check the rendered manifests:

```bash
kubectl kustomize rendered/pr-7
```

Then apply for real:

```bash
kubectl apply -k rendered/pr-7
kubectl -n preview-demo-pr-7 rollout status deployment/app --timeout=120s
kubectl port-forward -n preview-demo-pr-7 svc/app 8080:80
# → curl localhost:8080   "hello from a preview environment (version=dev)"
```

## 5. Teardown

```bash
previewctl teardown --repo octocat/demo --pr 7 --dry-run   # shows the target
previewctl teardown --repo octocat/demo --pr 7
kubectl get namespaces -l preview.env/platform=true        # Terminating, then gone
```

## 6. Cleanup: CronJob and TTL

Build the cleanup image, load it, and install `k8s/cleanup` with the image
pointed at the local build:

```bash
docker build -f Dockerfile.previewctl -t previewctl:0.1.0 .
kind load docker-image previewctl:0.1.0 --name preview-e2e

mkdir -p rendered/cleanup-local
cat > rendered/cleanup-local/kustomization.yaml <<'EOF'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../k8s/cleanup
images:
  - name: ghcr.io/tenalisriharsha/preview-env-platform/previewctl
    newName: previewctl
    newTag: "0.1.0"
EOF
kubectl apply -k rendered/cleanup-local
```

Run the CronJob once instead of waiting for the hour. With `--ttl 24h`
nothing in a fresh cluster is old enough:

```bash
kubectl -n previewctl-system create job cleanup-now --from=cronjob/preview-cleanup
kubectl -n previewctl-system wait --for=condition=complete job/cleanup-now --timeout=120s
kubectl -n previewctl-system logs job/cleanup-now     # nothing to clean up
```

To watch the TTL work, use a short one from your workstation. The API
server sets a namespace's `creationTimestamp` and it cannot be backdated,
so bring PR 7 back (step 5 removed it), wait a minute, create PR 8, and
use a TTL between the two ages:

```bash
kubectl wait --for=delete namespace/preview-demo-pr-7 --timeout=120s
kubectl apply -k rendered/pr-7
sleep 60
previewctl render --repo octocat/demo --pr 8 --image preview-app --tag pr-7 \
  --base ./k8s/base --domain preview.localtest.me --out rendered/pr-8
kubectl apply -k rendered/pr-8
kubectl get namespaces -l preview.env/platform=true   # pr-7 is ~1m older than pr-8
previewctl cleanup --ttl 45s --dry-run   # would delete preview-demo-pr-7 only
previewctl cleanup --ttl 45s             # deleted preview-demo-pr-7
kubectl get namespaces -l preview.env/platform=true   # pr-8 still Active
```

The CronJob's ServiceAccount cannot delete anything that is not a
preview namespace:

```bash
kubectl create namespace not-a-preview
kubectl delete namespace not-a-preview \
  --as=system:serviceaccount:previewctl-system:preview-cleanup   # denied by the admission policy
kubectl delete namespace not-a-preview
```

## Notes

- `kubectl apply --dry-run=client` still talks to the cluster for API
  discovery, so for a clusterless check use `kubectl kustomize` (step 4)
  or `--dry-run=server` against kind.
- The manifest test suite (`pytest tests/test_manifests.py`) runs the
  clusterless `kubectl kustomize` validation automatically when `kubectl`
  is on the PATH.
- With the Docker runtime (k3s `--docker`), `kubectl port-forward` needs
  `socat` on the node.
- `preview.localtest.me` resolves to `127.0.0.1` via a public wildcard DNS,
  which is handy with a local ingress controller; plain `port-forward`
  (above) needs neither.
