"""Structural tests for the k8s manifests and GitHub Actions workflows.

These are stdlib-only checks (no PyYAML dependency): they verify that the
Kustomize base is internally consistent, that the assumptions made by
``previewctl render`` hold (an Ingress to patch, an image name to retag),
and that the workflows wire the right previewctl commands together.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = REPO_ROOT / "k8s" / "base"

from previewctl import render as render_mod  # noqa: E402


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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
