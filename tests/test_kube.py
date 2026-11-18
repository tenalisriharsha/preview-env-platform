"""Tests for the kubectl wrapper, using a fake ``kubectl`` on PATH."""

import os
import signal
import stat
import threading
from datetime import datetime, timezone

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


class TestRunTimeout:
    def test_hanging_kubectl_does_not_block_forever(self, tmp_path, monkeypatch):
        # A hung kubectl (network partition, unresponsive API server) must
        # not be able to block the caller indefinitely. The fake process
        # below hangs essentially forever (sleep 999999s) rather than
        # exiting on its own, so a passing test proves _run enforces its
        # own timeout rather than happening to outrun a fixed sleep.
        fake_kubectl(
            tmp_path,
            monkeypatch,
            'echo $$ > "$(dirname "$0")/pid"\nexec sleep 999999\n',
        )

        result = {}

        def call():
            try:
                kube.list_preview_environments()
                result["status"] = "ok"
            except kube.KubectlError:
                result["status"] = "error"
            except Exception as exc:  # noqa: BLE001 - diagnostic only, see assert below
                # Anything other than KubectlError means a raw timeout
                # exception (e.g. subprocess.TimeoutExpired) leaked out
                # instead of being translated.
                result["status"] = "crashed"
                result["exc"] = repr(exc)

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        thread.join(timeout=15)
        hung = thread.is_alive()

        pid_file = tmp_path / "pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

        assert not hung, (
            "kube._run passed no timeout to subprocess.run, so it blocked "
            "indefinitely on a hanging kubectl instead of failing fast"
        )
        assert result.get("status") == "error", (
            f"expected a KubectlError, got {result!r} instead — a timeout "
            "must be translated into KubectlError, not left to leak out raw"
        )


class TestDeleteNamespace:
    def test_refuses_non_preview_namespace(self):
        with pytest.raises(ValueError, match="refusing"):
            kube.delete_namespace("kube-system")

    def test_deletes_and_reports_true(self, tmp_path, monkeypatch):
        fake_kubectl(
            tmp_path,
            monkeypatch,
            'echo "$@" > "$(dirname "$0")/args"\n'
            'echo \'namespace "preview-a-pr-1" deleted\'\n',
        )
        assert kube.delete_namespace("preview-a-pr-1") is True
        assert "--ignore-not-found" in (tmp_path / "args").read_text()

    def test_missing_namespace_is_not_an_error(self, tmp_path, monkeypatch):
        # kubectl delete --ignore-not-found exits 0 and prints nothing.
        fake_kubectl(tmp_path, monkeypatch, "exit 0\n")
        assert kube.delete_namespace("preview-a-pr-1") is False


class TestAnnotateLastDeployed:
    def test_refuses_non_preview_namespace(self):
        with pytest.raises(ValueError, match="refusing"):
            kube.annotate_last_deployed("kube-system")

    def test_stamps_the_given_time(self, tmp_path, monkeypatch):
        fake_kubectl(
            tmp_path, monkeypatch, 'echo "$@" > "$(dirname "$0")/args"\n'
        )
        kube.annotate_last_deployed(
            "preview-a-pr-1", now=datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
        )
        args = (tmp_path / "args").read_text()
        assert "annotate namespace preview-a-pr-1" in args
        assert "preview.env/last-deployed=2026-08-06T12:00:00Z" in args
        assert "--overwrite" in args
