# Local end-to-end dry run

How to exercise the whole deploy/teardown path by hand against a local
[kind](https://kind.sigs.k8s.io/) cluster — the same steps the GitHub Actions
workflows run, minus the PR comment.

Prerequisites: `kind`, `kubectl`, `docker`, and the package installed
(`pip install -e .`).

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
(`pr-7.preview.localtest.me`), and writes `rendered/pr-7/kustomization.yaml`.
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
kubectl get namespaces -l preview.env/platform=true        # gone
```

## Notes

- `kubectl apply --dry-run=client` still talks to the cluster for API
  discovery, so for a clusterless check use `kubectl kustomize` (step 4)
  or `--dry-run=server` against kind.
- The manifest test suite (`pytest tests/test_manifests.py`) runs the
  clusterless `kubectl kustomize` validation automatically when `kubectl`
  is on the PATH.
- `preview.localtest.me` resolves to `127.0.0.1` via a public wildcard DNS,
  which is handy with a local ingress controller; plain `port-forward`
  (above) needs neither.
