"""Structural tests for the k8s manifests and GitHub Actions workflows.

Most of these are stdlib-only checks: they verify that the Kustomize base
is internally consistent, that the assumptions made by ``previewctl
render`` hold (an Ingress to patch, an image name to retag), and that the
workflows wire the right previewctl commands together. A few manifest
fields (securityContext) are checked structurally via PyYAML, as in
test_cleanup_manifests.py, since regexing nested YAML is fragile.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = REPO_ROOT / "k8s" / "base"

from previewctl import render as render_mod  # noqa: E402


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _deployment() -> dict:
    return yaml.safe_load(_read(BASE_DIR / "deployment.yaml"))


class TestKustomizeBase:
    def test_all_kustomization_resources_exist(self):
        kustomization = _read(BASE_DIR / "kustomization.yaml")
        resources = re.findall(r"^\s+-\s+(\S+\.yaml)\s*$", kustomization, re.M)
        assert resources, "kustomization.yaml lists no resources"
        for resource in resources:
            assert (BASE_DIR / resource).is_file(), f"missing base resource {resource}"

    def test_base_has_ingress_for_render_patch(self):
        # render_kustomization emits a patch targeting kind: Ingress.
        ingress = _read(BASE_DIR / "ingress.yaml")
        assert "kind: Ingress" in ingress
        assert "host:" in ingress  # placeholder host to be replaced

    def test_base_image_name_is_stable(self):
        # The workflow passes --image so kustomize retags this exact name.
        deployment = _read(BASE_DIR / "deployment.yaml")
        assert re.search(r"image:\s*preview-app:", deployment)

    def test_deployment_service_and_ingress_are_linked(self):
        deployment = _read(BASE_DIR / "deployment.yaml")
        service = _read(BASE_DIR / "service.yaml")
        ingress = _read(BASE_DIR / "ingress.yaml")
        port = re.search(r"containerPort:\s*(\d+)", deployment).group(1)
        assert f"targetPort: {port}" in service
        assert "name: app" in service and "name: app" in ingress


class TestDeploymentSecurity:
    """The preview app Deployment is the workload exposed on a public
    Ingress host, so it must be at least as locked down as the cleanup
    CronJob (see tests/test_cleanup_manifests.py::TestCronJob)."""

    def test_pod_security_context_is_non_root(self):
        pod = _deployment()["spec"]["template"]["spec"]
        sc = pod["securityContext"]
        assert sc["runAsNonRoot"] is True
        assert sc["runAsUser"] != 0
        assert sc["seccompProfile"] == {"type": "RuntimeDefault"}

    def test_container_security_context_is_locked_down(self):
        container = _deployment()["spec"]["template"]["spec"]["containers"][0]
        sc = container["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["readOnlyRootFilesystem"] is True
        assert sc["capabilities"] == {"drop": ["ALL"]}


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
class TestKustomizeBuild:
    """Smoke tests using kubectl's built-in kustomize (no cluster needed)."""

    def _kustomize(self, directory: Path) -> str:
        proc = subprocess.run(
            ["kubectl", "kustomize", str(directory)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    def test_base_builds(self):
        output = self._kustomize(BASE_DIR)
        assert "kind: Deployment" in output
        assert "kind: Service" in output
        assert "kind: Ingress" in output

    def test_rendered_overlay_builds_with_pr_values(self, tmp_path):
        # kubectl kustomize rejects absolute resource paths, so reference
        # the base relatively, as the workflow does with ./k8s/base.
        content = render_mod.render_kustomization(
            namespace="preview-demo-pr-7",
            base=os.path.relpath(BASE_DIR, tmp_path),
            image="preview-app",
            tag="pr-7",
            pr=7,
            host="pr-7.preview.example.com",
        )
        render_mod.write_overlay(tmp_path, content, "preview-demo-pr-7")
        output = self._kustomize(tmp_path)
        assert "namespace: preview-demo-pr-7" in output
        assert "preview-app:pr-7" in output
        assert "pr-7.preview.example.com" in output
        assert 'preview.env/pr: "7"' in output

    def test_workflow_image_replaces_the_base_image(self, tmp_path):
        # The workflow pushes ghcr.io/<repo>/preview-app:pr-<N>; the built
        # Deployment must reference exactly that, not the base preview-app.
        content = render_mod.render_kustomization(
            namespace="preview-demo-pr-7",
            base=os.path.relpath(BASE_DIR, tmp_path),
            image="preview-app",
            new_name="ghcr.io/octocat/demo/preview-app",
            tag="pr-7",
            pr=7,
            host="pr-7.preview.example.com",
        )
        render_mod.write_overlay(tmp_path, content, "preview-demo-pr-7")
        output = self._kustomize(tmp_path)
        assert re.findall(r"image: (\S+)", output) == [
            "ghcr.io/octocat/demo/preview-app:pr-7"
        ]

    def test_rendered_overlay_creates_labelled_namespace(self, tmp_path):
        # teardown/cleanup select namespaces by preview.env/platform=true, so
        # the Namespace object itself must be in the build and carry the label.
        content = render_mod.render_kustomization(
            namespace="preview-demo-pr-7",
            base=os.path.relpath(BASE_DIR, tmp_path),
            image="preview-app",
            tag="pr-7",
            pr=7,
            host="pr-7.preview.example.com",
        )
        render_mod.write_overlay(tmp_path, content, "preview-demo-pr-7")
        output = self._kustomize(tmp_path)
        (namespace_doc,) = [
            doc for doc in output.split("---\n") if "kind: Namespace" in doc
        ]
        assert "name: preview-demo-pr-7" in namespace_doc
        assert 'preview.env/platform: "true"' in namespace_doc
        assert 'preview.env/pr: "7"' in namespace_doc


class TestWorkflows:
    WORKFLOWS = REPO_ROOT / ".github" / "workflows"

    def test_preview_workflow_wires_the_pipeline(self):
        workflow = _read(self.WORKFLOWS / "preview.yaml")
        for step in ("docker build", "previewctl render", "kubectl apply -k",
                     "previewctl comment"):
            assert step in workflow, f"preview.yaml is missing {step!r}"
        assert "types: [opened, synchronize, reopened]" in workflow
        assert "pull-requests: write" in workflow

    def test_preview_workflow_retags_the_base_image_name(self):
        # kustomize only rewrites images whose name matches the base, so
        # --image must be the base name and the registry path goes in
        # --new-name; otherwise the cluster pulls preview-app:latest.
        workflow = _read(self.WORKFLOWS / "preview.yaml")
        base_image = re.search(
            r"image:\s*([^:\s]+):", _read(BASE_DIR / "deployment.yaml")
        ).group(1)
        assert f"--image {base_image} " in workflow
        assert '--new-name "$IMAGE"' in workflow

    def test_cluster_steps_are_skipped_when_no_kubeconfig_secret_is_set(self):
        # Without a cluster (no KUBECONFIG secret, or a fork pull request) the
        # workflows used to fail on `kubectl apply` against localhost:8080 and
        # leave every PR red. Every step after checkout must be gated on the
        # secret, and a notice step must say why nothing ran.
        for name in ("preview.yaml", "teardown.yaml"):
            workflow = _read(self.WORKFLOWS / name)
            assert "HAS_CLUSTER: ${{ secrets.KUBECONFIG != '' }}" in workflow, name
            steps = re.split(r"(?m)^(?=      - )", workflow.split("    steps:\n", 1)[1])
            steps = [s for s in steps if s.startswith("      - ")]
            checkout, rest = steps[0], steps[1:]
            assert "actions/checkout" in checkout, name
            assert "if:" not in checkout, f"{name}: checkout must always run"
            notice = [s for s in rest if "env.HAS_CLUSTER != 'true'" in s]
            assert len(notice) == 1, f"{name}: expected one skip-notice step"
            for step in rest:
                if step in notice:
                    continue
                assert "if: env.HAS_CLUSTER == 'true'" in step, (
                    f"{name}: step is not gated on the cluster: {step.splitlines()[0]}"
                )

    def test_teardown_workflow(self):
        workflow = _read(self.WORKFLOWS / "teardown.yaml")
        assert "types: [closed]" in workflow
        assert "previewctl teardown" in workflow

    def test_rbac_scopes_permissions(self):
        rbac = _read(REPO_ROOT / "k8s" / "rbac.yaml")
        assert "kind: ServiceAccount" in rbac
        assert 'resources: ["namespaces"]' in rbac
        # Least privilege: no access to secrets or cluster config.
        assert "secrets" not in rbac
