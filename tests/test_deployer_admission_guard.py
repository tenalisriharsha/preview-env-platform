"""Regression test for issue #8.

The preview-deployer ClusterRole (k8s/rbac.yaml) grants `delete` on
*any* namespace. Only previewctl's own prefix/label check keeps the
deployer from touching unrelated namespaces (see previewctl/kube.py) -
there is no server-side guard, unlike the cleanup ServiceAccount, which
already has a ValidatingAdmissionPolicy (k8s/cleanup/admission-policy.yaml)
restricting it to preview-* namespaces labelled preview.env/platform=true.

This test asserts an equivalent policy exists for the deployer SA. It
is implementation-location agnostic: it scans every manifest under k8s/
for a ValidatingAdmissionPolicy whose matchConditions reference the
preview-deployer service account, rather than assuming a specific file.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from previewctl.cleanup import PLATFORM_LABEL

REPO_ROOT = Path(__file__).resolve().parent.parent
K8S_DIR = REPO_ROOT / "k8s"
RBAC_FILE = K8S_DIR / "rbac.yaml"


def _docs_in(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _all_k8s_docs() -> list[dict]:
    docs = []
    for path in K8S_DIR.rglob("*.yaml"):
        docs.extend(_docs_in(path))
    return docs


def _deployer_sa_username() -> str:
    (sa,) = [d for d in _docs_in(RBAC_FILE) if d["kind"] == "ServiceAccount"]
    return f"system:serviceaccount:{sa['metadata']['namespace']}:{sa['metadata']['name']}"


def _deployer_admission_policy() -> dict:
    username = _deployer_sa_username()
    policies = [d for d in _all_k8s_docs() if d["kind"] == "ValidatingAdmissionPolicy"]
    matches = [
        p
        for p in policies
        if any(
            username in c.get("expression", "")
            for c in p["spec"].get("matchConditions", [])
        )
    ]
    assert len(matches) == 1, (
        f"expected exactly one ValidatingAdmissionPolicy guarding {username}'s "
        f"namespace deletes under k8s/, found {len(matches)}"
    )
    return matches[0]


class TestDeployerCanDeleteAnyNamespaceViaRbacAlone:
    def test_cluster_role_grants_delete_on_all_namespaces(self):
        # Sanity-check the premise of the issue: RBAC itself cannot scope
        # "namespaces" to a name pattern, so delete is unrestricted.
        (role,) = [d for d in _docs_in(RBAC_FILE) if d["kind"] == "ClusterRole"]
        (rule,) = [r for r in role["rules"] if r["resources"] == ["namespaces"]]
        assert "delete" in rule["verbs"]


class TestDeployerAdmissionGuard:
    def test_namespace_deletes_by_the_deployer_are_guarded(self):
        policy = _deployer_admission_policy()["spec"]
        (rule,) = policy["matchConstraints"]["resourceRules"]
        assert rule["operations"] == ["DELETE"]
        assert rule["resources"] == ["namespaces"]
        assert policy["failurePolicy"] == "Fail"

    def test_guard_requires_preview_prefix_and_platform_label(self):
        policy = _deployer_admission_policy()["spec"]
        (validation,) = policy["validations"]
        expression = validation["expression"]
        assert "startsWith('preview-')" in expression
        assert PLATFORM_LABEL in expression

    def test_guard_binding_denies(self):
        policy_name = _deployer_admission_policy()["metadata"]["name"]
        bindings = [
            d
            for d in _all_k8s_docs()
            if d["kind"] == "ValidatingAdmissionPolicyBinding"
            and d["spec"]["policyName"] == policy_name
        ]
        assert len(bindings) == 1, (
            f"expected exactly one binding for policy {policy_name!r}, "
            f"found {len(bindings)}"
        )
        assert bindings[0]["spec"]["validationActions"] == ["Deny"]
