"""Thin kubectl wrapper — the only module that talks to a cluster.

Everything here is deliberately an I/O shell over the pure logic in
:mod:`previewctl.cleanup` and :mod:`previewctl.naming`; its tests run it
against a fake ``kubectl`` on PATH (``tests/test_kube.py``).
"""

import subprocess
from datetime import datetime, timezone

from .cleanup import LAST_DEPLOYED_ANNOTATION, PLATFORM_LABEL, parse_namespace_list


class KubectlError(RuntimeError):
    pass


def _run(args: list[str]) -> bytes:
    try:
        proc = subprocess.run(
            ["kubectl", *args], capture_output=True, check=False
        )
    except FileNotFoundError:
        raise KubectlError("kubectl not found on PATH") from None
    if proc.returncode != 0:
        raise KubectlError(proc.stderr.decode().strip() or "kubectl failed")
    return proc.stdout


def list_preview_environments():
    """Return all namespaces carrying the preview platform label."""
    raw = _run(
        ["get", "namespaces", "-l", f"{PLATFORM_LABEL}=true", "-o", "json"]
    )
    return parse_namespace_list(raw)


def delete_namespace(namespace: str) -> bool:
    """Delete a namespace; refuses anything outside the preview prefix.

    Returns False when the namespace did not exist (e.g. the deploy never
    got as far as creating it), so teardown is idempotent.
    """
    if not namespace.startswith("preview-"):
        raise ValueError(f"refusing to delete non-preview namespace {namespace!r}")
    out = _run(["delete", "namespace", namespace, "--wait=false", "--ignore-not-found"])
    return bool(out.strip())


def annotate_last_deployed(namespace: str, now: datetime | None = None) -> None:
    """Stamp a namespace with the current time as its last deploy.

    Called on every deploy so ``previewctl cleanup`` can measure the TTL
    from the last push instead of from when the namespace was first
    created; refuses anything outside the preview prefix, same as
    :func:`delete_namespace`.
    """
    if not namespace.startswith("preview-"):
        raise ValueError(f"refusing to annotate non-preview namespace {namespace!r}")
    now = now or datetime.now(timezone.utc)
    timestamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _run(
        [
            "annotate",
            "namespace",
            namespace,
            f"{LAST_DEPLOYED_ANNOTATION}={timestamp}",
            "--overwrite",
        ]
    )
