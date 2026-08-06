"""Thin kubectl wrapper — the only module that talks to a cluster.

Everything here is deliberately an I/O shell over the pure logic in
:mod:`previewctl.cleanup` and :mod:`previewctl.naming`, so it stays out of
the unit-test path (it is exercised by the local kind e2e in Phase 2).
"""

import subprocess

from .cleanup import PLATFORM_LABEL, parse_namespace_list


class KubectlError(RuntimeError):
    pass


def _run(args: list[str]) -> bytes:
    proc = subprocess.run(
        ["kubectl", *args], capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise KubectlError(proc.stderr.decode().strip() or "kubectl failed")
    return proc.stdout


def list_preview_environments():
    """Return all namespaces carrying the preview platform label."""
    raw = _run(
        ["get", "namespaces", "-l", f"{PLATFORM_LABEL}=true", "-o", "json"]
    )
    return parse_namespace_list(raw)


def delete_namespace(namespace: str) -> None:
    """Delete a namespace; refuses anything outside the preview prefix."""
    if not namespace.startswith("preview-"):
        raise ValueError(f"refusing to delete non-preview namespace {namespace!r}")
    _run(["delete", "namespace", namespace, "--wait=false"])


def apply_overlay(directory: str) -> None:
    """``kubectl apply -k`` a rendered overlay directory."""
    _run(["apply", "-k", directory])
