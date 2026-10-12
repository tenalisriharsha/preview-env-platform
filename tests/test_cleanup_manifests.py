"""Tests for the cleanup CronJob manifests (k8s/cleanup) and its image.

The manifests are parsed with PyYAML (a dev-only dependency; previewctl
itself stays stdlib-only). The live behaviour — the Job running under
Pod Security "restricted", the admission policy denying non-preview
deletes — was checked against a real cluster; see docs/local-e2e.md.
"""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from previewctl.cleanup import PLATFORM_LABEL
from previewctl.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent
CLEANUP_DIR = REPO_ROOT / "k8s" / "cleanup"
DOCKERFILE = REPO_ROOT / "Dockerfile.previewctl"
PUBLISHED_IMAGE = "ghcr.io/tenalisriharsha/preview-env-platform/previewctl:0.1.0"


def _docs(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def _all_docs() -> list[dict]:
    kustomization = yaml.safe_load((CLEANUP_DIR / "kustomization.yaml").read_text())
    docs = []
    for resource in kustomization["resources"]:
        docs.extend(_docs(CLEANUP_DIR / resource))
    return docs


def _one(kind: str, docs: list[dict] | None = None) -> dict:
    matches = [d for d in (docs or _all_docs()) if d["kind"] == kind]
    assert len(matches) == 1, f"expected exactly one {kind}, got {len(matches)}"
    return matches[0]


def _pod_spec(cronjob: dict) -> dict:
    return cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def _sa_username() -> str:
    sa = _one("ServiceAccount")
    return f"system:serviceaccount:{sa['metadata']['namespace']}:{sa['metadata']['name']}"


class TestKustomization:
    def test_lists_every_manifest_in_the_directory(self):
        kustomization = yaml.safe_load((CLEANUP_DIR / "kustomization.yaml").read_text())
        on_disk = {p.name for p in CLEANUP_DIR.glob("*.yaml")} - {"kustomization.yaml"}
        assert set(kustomization["resources"]) == on_disk

    def test_contains_exactly_the_expected_objects(self):
        kinds = sorted(d["kind"] for d in _all_docs())
        assert kinds == sorted([
            "Namespace", "ServiceAccount", "ClusterRole", "ClusterRoleBinding",
            "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding",
            "CronJob",
        ])

    def test_maps_the_image_to_the_published_name(self):
        kustomization = yaml.safe_load((CLEANUP_DIR / "kustomization.yaml").read_text())
        (image,) = kustomization["images"]
        container = _pod_spec(_one("CronJob"))["containers"][0]
        assert image["name"] == container["image"] == "previewctl"
        assert f"{image['newName']}:{image['newTag']}" == PUBLISHED_IMAGE

    def test_namespaced_objects_share_the_cronjob_namespace(self):
        namespace = _one("Namespace")["metadata"]["name"]
        for kind in ("ServiceAccount", "CronJob"):
            assert _one(kind)["metadata"]["namespace"] == namespace


class TestNamespace:
    def test_is_never_selected_by_teardown_or_cleanup(self):
        meta = _one("Namespace")["metadata"]
        # previewctl only deletes preview-* names carrying the platform label.
        assert not meta["name"].startswith("preview-")
        assert PLATFORM_LABEL not in meta.get("labels", {})

    def test_enforces_restricted_pod_security(self):
        labels = _one("Namespace")["metadata"]["labels"]
        assert labels["pod-security.kubernetes.io/enforce"] == "restricted"


class TestCronJob:
    def test_runs_cleanup_hourly_with_a_24h_ttl(self):
        cronjob = _one("CronJob")
        assert cronjob["spec"]["schedule"] == "0 * * * *"
        container = _pod_spec(cronjob)["containers"][0]
        assert container["args"] == ["cleanup", "--ttl", "24h"]

    def test_never_overlaps_and_keeps_bounded_history(self):
        spec = _one("CronJob")["spec"]
        assert spec["concurrencyPolicy"] == "Forbid"
        assert 1 <= spec["successfulJobsHistoryLimit"] <= 5
        assert 1 <= spec["failedJobsHistoryLimit"] <= 5
        job = spec["jobTemplate"]["spec"]
        assert job["activeDeadlineSeconds"] > 0
        assert job["backoffLimit"] <= 2

    def test_uses_the_cleanup_service_account(self):
        pod = _pod_spec(_one("CronJob"))
        assert pod["serviceAccountName"] == _one("ServiceAccount")["metadata"]["name"]
        assert pod["restartPolicy"] == "Never"

    def test_sets_requests_and_limits(self):
        resources = _pod_spec(_one("CronJob"))["containers"][0]["resources"]
        for section in ("requests", "limits"):
            assert set(resources[section]) == {"cpu", "memory"}

    def test_pod_security_context_is_non_root(self):
        pod = _pod_spec(_one("CronJob"))
        sc = pod["securityContext"]
        assert sc["runAsNonRoot"] is True
        assert sc["runAsUser"] != 0
        assert sc["seccompProfile"] == {"type": "RuntimeDefault"}

    def test_container_security_context_is_locked_down(self):
        sc = _pod_spec(_one("CronJob"))["containers"][0]["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["readOnlyRootFilesystem"] is True
        assert sc["capabilities"] == {"drop": ["ALL"]}

    def test_writable_tmp_backs_the_read_only_root(self):
        # The image sets HOME=/tmp for kubectl's cache; /tmp must be writable.
        pod = _pod_spec(_one("CronJob"))
        (mount,) = pod["containers"][0]["volumeMounts"]
        assert mount["mountPath"] == "/tmp"
        (volume,) = pod["volumes"]
        assert volume["name"] == mount["name"] and "emptyDir" in volume
        assert "ENV HOME=/tmp" in DOCKERFILE.read_text()


class TestRbac:
    def test_cluster_role_is_namespaces_get_list_delete_only(self):
        assert _one("ClusterRole")["rules"] == [
            {"apiGroups": [""], "resources": ["namespaces"],
             "verbs": ["get", "list", "delete"]}
        ]

    def test_binding_ties_the_role_to_the_service_account(self):
        binding = _one("ClusterRoleBinding")
        sa = _one("ServiceAccount")["metadata"]
        assert binding["roleRef"] == {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": _one("ClusterRole")["metadata"]["name"],
        }
        assert binding["subjects"] == [
            {"kind": "ServiceAccount", "name": sa["name"], "namespace": sa["namespace"]}
        ]

    def test_role_covers_exactly_what_previewctl_cleanup_calls(self, tmp_path, monkeypatch):
        # Run the real `previewctl cleanup` against a fake kubectl that logs
        # every invocation, then map each call to the API verbs it needs:
        # `get namespaces -l ...` is a list; `delete namespace <name>` reads
        # the object before deleting it.
        log = tmp_path / "calls"
        fake = tmp_path / "kubectl"
        fake.write_text(
            "#!/bin/sh\n"
            f'echo "$@" >> "{log}"\n'
            'if [ "$1" = get ]; then\n'
            "  echo '{\"items\": [{\"metadata\": {\"name\": \"preview-a-pr-1\","
            ' "creationTimestamp": "2020-01-01T00:00:00Z",'
            ' "labels": {"preview.env/pr": "1"}}}]}\'\n'
            "else\n"
            '  echo \'namespace "preview-a-pr-1" deleted\'\n'
            "fi\n"
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        assert main(["cleanup", "--ttl", "24h"]) == 0

        needed = set()
        for call in log.read_text().splitlines():
            verb, resource, *rest = call.split()
            assert resource in ("namespace", "namespaces"), call
            if verb == "get":
                assert "-l" in rest, call  # never an unfiltered or named get
                needed.add("list")
            elif verb == "delete":
                needed.update({"get", "delete"})
            else:
                pytest.fail(f"unexpected kubectl call: {call}")
        (rule,) = _one("ClusterRole")["rules"]
        assert set(rule["verbs"]) == needed


class TestAdmissionPolicy:
    def test_matches_namespace_deletes_by_the_cleanup_sa_only(self):
        policy = _one("ValidatingAdmissionPolicy")["spec"]
        (rule,) = policy["matchConstraints"]["resourceRules"]
        assert rule["operations"] == ["DELETE"]
        assert rule["resources"] == ["namespaces"]
        (condition,) = policy["matchConditions"]
        assert f"'{_sa_username()}'" in condition["expression"]
        assert policy["failurePolicy"] == "Fail"

    def test_requires_prefix_and_platform_label(self):
        (validation,) = _one("ValidatingAdmissionPolicy")["spec"]["validations"]
        expression = validation["expression"]
        assert "oldObject.metadata.name.startsWith('preview-')" in expression
        assert f"oldObject.metadata.labels['{PLATFORM_LABEL}'] == 'true'" in expression

    def test_binding_denies(self):
        binding = _one("ValidatingAdmissionPolicyBinding")["spec"]
        assert binding["policyName"] == _one("ValidatingAdmissionPolicy")["metadata"]["name"]
        assert binding["validationActions"] == ["Deny"]


class TestImage:
    def test_runs_previewctl_as_the_cronjob_user(self):
        dockerfile = DOCKERFILE.read_text()
        user = re.search(r"^USER (\d+):(\d+)$", dockerfile, re.M)
        sc = _pod_spec(_one("CronJob"))["securityContext"]
        assert (int(user.group(1)), int(user.group(2))) == (sc["runAsUser"], sc["runAsGroup"])
        assert 'ENTRYPOINT ["previewctl"]' in dockerfile

    def test_kubectl_download_is_checksum_pinned_per_arch(self):
        dockerfile = DOCKERFILE.read_text()
        for arch in ("amd64", "arm64"):
            assert re.search(rf"^ARG KUBECTL_SHA256_{arch}=[0-9a-f]{{64}}$", dockerfile, re.M)
        assert "sha256sum -c -" in dockerfile

    def test_ci_builds_the_image(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yaml").read_text()
        assert "docker build -f Dockerfile.previewctl" in ci


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
class TestKustomizeBuild:
    def test_builds_with_the_published_image(self):
        proc = subprocess.run(
            ["kubectl", "kustomize", str(CLEANUP_DIR)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr
        docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
        container = _pod_spec(_one("CronJob", docs))["containers"][0]
        assert container["image"] == PUBLISHED_IMAGE
        assert len(docs) == 7
