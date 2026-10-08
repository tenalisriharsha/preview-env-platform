"""Tests for the kubectl wrapper, using a fake ``kubectl`` on PATH."""

import os
import stat

import pytest

from previewctl import kube


def fake_kubectl(tmp_path, monkeypatch, script: str):
    """Put an executable ``kubectl`` shell script first on PATH."""
    path = tmp_path / "kubectl"
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return path


class TestRun:
    def test_missing_kubectl_raises_kubectl_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))  # empty dir: no kubectl
        with pytest.raises(kube.KubectlError, match="kubectl not found on PATH"):
            kube.list_preview_environments()

    def test_nonzero_exit_raises_with_stderr(self, tmp_path, monkeypatch):
        fake_kubectl(tmp_path, monkeypatch, 'echo "connection refused" >&2\nexit 1\n')
        with pytest.raises(kube.KubectlError, match="connection refused"):
            kube.list_preview_environments()

    def test_lists_labelled_namespaces(self, tmp_path, monkeypatch):
        fake_kubectl(
            tmp_path,
            monkeypatch,
            'echo "$@" > "$(dirname "$0")/args"\n'
            "echo '{\"items\": [{\"metadata\": {\"name\": \"preview-a-pr-1\","
            ' "creationTimestamp": "2026-08-05T10:00:00Z",'
            ' "labels": {"preview.env/pr": "1"}}}]}\'\n',
        )
        (env,) = kube.list_preview_environments()
        assert env.namespace == "preview-a-pr-1" and env.pr == 1
        args = (tmp_path / "args").read_text()
        assert "-l preview.env/platform=true" in args


class TestDeleteNamespace:
    def test_refuses_non_preview_namespace(self):
        with pytest.raises(ValueError, match="refusing"):
            kube.delete_namespace("kube-system")
