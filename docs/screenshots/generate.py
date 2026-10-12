"""Regenerate the README screenshots from real command output.

Every terminal image is produced by running the exact command it displays
(through ``bash -c``, from the repository root) and capturing its real
stdout/stderr and exit code; nothing is typed or edited by hand. The PR
comment image renders the real ``build_comment()`` markdown with cmark-gfm
(GitHub's Markdown library), using the hard line breaks GitHub applies to
comments.

Requirements: ``pip install -e .[screenshots]`` (puts ``previewctl`` on PATH)
and ``kubectl`` on PATH. Run from anywhere:

    python docs/screenshots/generate.py

``--cluster`` additionally captures the live cleanup flow (11-16) against
the cluster in the current kubectl context. It needs ``k8s/cleanup``
installed there (see docs/local-e2e.md) and refuses to run if any preview
namespace already exists, because it runs ``previewctl cleanup --ttl 1m``
for real. Use a throwaway cluster:

    python docs/screenshots/generate.py --cluster

The cluster setup it does before capturing (creating two preview
namespaces 75s apart, plus one ordinary namespace) is not shown in the
images; every command that is shown ran exactly as displayed.
"""

import argparse
import html
import os
import tempfile
import time
import shutil
import subprocess
import sys
from pathlib import Path

import cmarkgfm
from cmarkgfm.cmark import Options
from playwright.sync_api import sync_playwright

from previewctl import comment, naming, render
from previewctl.cli import DEFAULT_DOMAIN

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "docs" / "screenshots"
REPO = "tenalisriharsha/preview-env-platform"
IMAGE = f"ghcr.io/{REPO}/preview-app"
MAX_LINES = 40  # longer outputs are cut with an explicit truncation marker

# (file, window title, command). Commands run in order from REPO_ROOT, so
# 03 reads the overlay that 02 wrote.
TERMINAL_SHOTS = [
    ("01-name.png", "previewctl name", f"previewctl name --repo {REPO} --pr 42"),
    (
        "02-render.png",
        "previewctl render",
        f"previewctl render --repo {REPO} --pr 42 --image preview-app"
        f" --new-name {IMAGE} --base k8s/base --out rendered/pr-42",
    ),
    (
        "03-kustomize-build.png",
        "kubectl kustomize",
        "kubectl kustomize rendered/pr-42 | awk '/kind: Ingress/{f=1} f'",
    ),
    ("04-render-help.png", "previewctl render --help", "previewctl render --help"),
    ("06-comment-help.png", "previewctl comment --help", "previewctl comment --help"),
    (
        "07-comment-no-token.png",
        "previewctl comment (no GITHUB_TOKEN)",
        f"env -u GITHUB_TOKEN previewctl comment --repo {REPO} --pr 42 --sha a1b2c3d",
    ),
    ("08-cleanup-help.png", "previewctl cleanup --help", "previewctl cleanup --help"),
    (
        "09-cleanup-manifests.png",
        "kubectl kustomize k8s/cleanup",
        "kubectl kustomize k8s/cleanup | grep -E "
        "'^kind:|schedule:|concurrencyPolicy:|image:|- 24h|runAsUser:|readOnlyRootFilesystem:'",
    ),
    (
        "10-cleanup-rbac.png",
        "cleanup ClusterRole",
        "kubectl kustomize k8s/cleanup | awk '/^kind: ClusterRole$/{f=1} /^---/{f=0} f'",
    ),
]

CLEANUP_SA = "system:serviceaccount:previewctl-system:preview-cleanup"
OLD_PR, NEW_PR = 41, 42
LABEL_SELECTOR = "-l preview.env/platform=true"
DEMO_JOB = "cleanup-demo"
OTHER_NAMESPACE = "not-a-preview"

# Live cleanup flow, run in order against the current kubectl context
# after cluster_setup(): PR 41's namespace is >1m old, PR 42's is fresh.
CLUSTER_SHOTS = [
    (
        "11-cleanup-before.png",
        "preview namespaces before cleanup",
        f"kubectl get namespaces {LABEL_SELECTOR} -L preview.env/pr",
    ),
    ("12-cleanup-dry-run.png", "previewctl cleanup --dry-run",
     "previewctl cleanup --ttl 1m --dry-run"),
    ("13-cleanup.png", "previewctl cleanup", "previewctl cleanup --ttl 1m"),
    (
        "14-cleanup-after.png",
        "preview namespaces after cleanup",
        f"kubectl wait --for=delete namespace/{naming.namespace_for_pr(REPO, OLD_PR)}"
        f" --timeout=120s && kubectl get namespaces {LABEL_SELECTOR} -L preview.env/pr",
    ),
    (
        "15-cleanup-cronjob-run.png",
        "the in-cluster CronJob, run once by hand",
        f"kubectl -n previewctl-system create job {DEMO_JOB} --from=cronjob/preview-cleanup"
        f" && kubectl -n previewctl-system wait --for=condition=complete job/{DEMO_JOB}"
        f" --timeout=120s && kubectl -n previewctl-system logs job/{DEMO_JOB}",
    ),
    (
        "16-cleanup-admission-denied.png",
        "admission policy: cleanup SA cannot delete a non-preview namespace",
        f"kubectl delete namespace {OTHER_NAMESPACE} --as={CLEANUP_SA}",
    ),
]

TERMINAL_CSS = """
body { margin: 0; padding: 24px; background: #f6f8fa; width: max-content; }
.win { background: #0d1117; border: 1px solid #30363d; border-radius: 10px;
       min-width: 860px; max-width: 1100px; font: 14px/1.55 'DejaVu Sans Mono',
       Menlo, monospace; color: #e6edf3; overflow: hidden; }
.bar { background: #161b22; padding: 9px 14px; border-bottom: 1px solid #30363d;
       color: #8b949e; font-size: 12px; }
.dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%;
       margin-right: 6px; vertical-align: -1px; }
pre { margin: 0; padding: 16px 20px 18px; white-space: pre-wrap;
      word-break: break-all; font: inherit; }
.prompt { color: #58a6ff; } .ok { color: #3fb950; } .bad { color: #f85149; }
.trunc { color: #8b949e; }
"""

COMMENT_CSS = """
body { margin: 0; padding: 24px; background: #f6f8fa; width: max-content;
       font: 14px/1.5 -apple-system, 'Segoe UI', 'DejaVu Sans', sans-serif;
       color: #1f2328; }
.card { width: 700px; background: #fff; border: 1px solid #d1d9e0;
        border-radius: 6px; overflow: hidden; }
.head { background: #f6f8fa; border-bottom: 1px solid #d1d9e0; padding: 8px 16px;
        color: #59636e; }
.head b { color: #1f2328; }
.bot { border: 1px solid #d1d9e0; border-radius: 2em; padding: 0 6px;
       font-size: 12px; margin: 0 4px; }
.body { padding: 16px; }
h3 { margin: 0 0 16px; font-size: 1.25em; }
table { border-collapse: collapse; margin-bottom: 16px; }
th, td { border: 1px solid #d1d9e0; padding: 6px 13px; }
th { background: #fff; }
code { background: #eff1f3; border-radius: 6px; padding: .2em .4em;
       font: 85% 'DejaVu Sans Mono', monospace; }
p { margin: 0; }
"""


def run(command: str) -> tuple[str, int]:
    proc = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout + proc.stderr, proc.returncode


def terminal_html(title: str, command: str, output: str, code: int) -> str:
    lines = output.rstrip("\n").split("\n") if output.strip() else []
    body = [html.escape(line) for line in lines[:MAX_LINES]]
    if len(lines) > MAX_LINES:
        body.append('<span class="trunc">... (output truncated)</span>')
    status = "ok" if code == 0 else "bad"
    dots = "".join(
        f'<span class="dot" style="background:{c}"></span>'
        for c in ("#ff5f57", "#febc2e", "#28c840")
    )
    text = "\n".join(
        [f'<span class="prompt">$</span> {html.escape(command)}', *body,
         f'<span class="{status}">exit {code}</span>']
    )
    return (
        f"<html><head><style>{TERMINAL_CSS}</style></head><body>"
        f'<div class="win"><div class="bar">{dots}&nbsp;{html.escape(title)}</div>'
        f"<pre>{text}</pre></div></body></html>"
    )


def comment_html() -> str:
    markdown = comment.build_comment(
        namespace=naming.namespace_for_pr(REPO, 42),
        url=f"https://{render.preview_host(42, DEFAULT_DOMAIN)}",
        sha="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    )
    rendered = cmarkgfm.github_flavored_markdown_to_html(
        markdown, options=Options.CMARK_OPT_HARDBREAKS | Options.CMARK_OPT_UNSAFE
    )
    return (
        f"<html><head><style>{COMMENT_CSS}</style></head><body><div class=card>"
        '<div class="head"><b>github-actions</b><span class="bot">bot</span>'
        f'commented</div><div class="body">{rendered}</div></div></body></html>'
    )


def screenshot(page, markup: str, path: Path) -> None:
    page.set_content(markup)
    page.locator("body > div").screenshot(path=str(path))
    print(f"wrote {path.relative_to(REPO_ROOT)}")


def kubectl(*args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(
        ["kubectl", *args], input=stdin, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def create_preview_namespace(pr: int) -> None:
    """Create exactly the Namespace object a rendered overlay for ``pr`` holds."""
    with tempfile.TemporaryDirectory() as tmp:
        content = render.render_kustomization(
            namespace=naming.namespace_for_pr(REPO, pr),
            base=os.path.relpath(REPO_ROOT / "k8s" / "base", tmp),
            image="preview-app",
            tag=f"pr-{pr}",
            pr=pr,
            host=render.preview_host(pr, DEFAULT_DOMAIN),
        )
        render.write_overlay(tmp, content, naming.namespace_for_pr(REPO, pr))
        built = kubectl("kustomize", tmp)
    (namespace_doc,) = [d for d in built.split("---\n") if "kind: Namespace" in d]
    kubectl("apply", "-f", "-", stdin=namespace_doc)


def cluster_setup() -> None:
    if kubectl("get", "namespaces", *LABEL_SELECTOR.split(), "-o", "name").strip():
        raise RuntimeError(
            "preview namespaces already exist in this cluster; --cluster runs "
            "`previewctl cleanup --ttl 1m` for real, so use a throwaway cluster"
        )
    kubectl("-n", "previewctl-system", "get", "cronjob", "preview-cleanup")
    # An ordinary namespace: RBAC lets the cleanup SA delete namespaces, so
    # shot 16 shows the admission policy refusing it at the API server.
    kubectl("create", "namespace", OTHER_NAMESPACE)
    create_preview_namespace(OLD_PR)
    print(f"created PR {OLD_PR} namespace; waiting 75s so it outlives --ttl 1m")
    time.sleep(75)
    create_preview_namespace(NEW_PR)


def cluster_teardown() -> None:
    for args in (
        ("-n", "previewctl-system", "delete", "job", DEMO_JOB, "--ignore-not-found"),
        ("delete", "namespace", OTHER_NAMESPACE, naming.namespace_for_pr(REPO, OLD_PR),
         naming.namespace_for_pr(REPO, NEW_PR), "--ignore-not-found", "--wait=false"),
    ):
        try:
            kubectl(*args)
        except RuntimeError as exc:
            print(f"warning: cleanup after --cluster failed: {exc}", file=sys.stderr)


def capture(page, shots) -> None:
    for name, title, command in shots:
        output, code = run(command)
        screenshot(page, terminal_html(title, command, output, code), OUT_DIR / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--cluster", action="store_true",
        help="also capture the live cleanup flow (needs a throwaway cluster)",
    )
    args = parser.parse_args(argv)
    for tool in ("previewctl", "kubectl"):
        if shutil.which(tool) is None:
            print(f"error: {tool} not found on PATH", file=sys.stderr)
            return 1
    overlay = REPO_ROOT / "rendered" / "pr-42"
    created_overlay = not overlay.exists()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(device_scale_factor=2)
            capture(page, TERMINAL_SHOTS)
            screenshot(page, comment_html(), OUT_DIR / "05-pr-comment.png")
            if args.cluster:
                try:
                    cluster_setup()
                    capture(page, CLUSTER_SHOTS)
                finally:
                    cluster_teardown()
            browser.close()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if created_overlay:
            shutil.rmtree(overlay, ignore_errors=True)
            if not any(overlay.parent.iterdir()):
                overlay.parent.rmdir()
    return 0


if __name__ == "__main__":
    sys.exit(main())
