"""previewctl command-line interface.

Subcommands mirror the workflow steps:

    previewctl name      print the namespace for a PR
    previewctl render    write the per-PR Kustomize overlay
    previewctl comment   upsert the preview URL comment on the PR
    previewctl teardown  delete the PR's namespace
    previewctl cleanup   delete expired preview namespaces
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from . import cleanup as cleanup_mod
from . import comment as comment_mod
from . import kube
from . import naming
from . import render as render_mod

DEFAULT_DOMAIN = "preview.example.com"


def _cmd_name(args) -> int:
    print(naming.namespace_for_pr(args.repo, args.pr))
    return 0


def _cmd_render(args) -> int:
    namespace = naming.namespace_for_pr(args.repo, args.pr)
    host = render_mod.preview_host(args.pr, args.domain)
    content = render_mod.render_kustomization(
        namespace=namespace,
        base=args.base,
        image=args.image,
        tag=args.tag or f"pr-{args.pr}",
        pr=args.pr,
        host=host,
    )
    out = args.out or f"rendered/pr-{args.pr}"
    render_mod.write_overlay(out, content)
    print(f"namespace={namespace}")
    print(f"host={host}")
    print(f"overlay={out}/kustomization.yaml")
    return 0


def _cmd_comment(args) -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("error: GITHUB_TOKEN is not set", file=sys.stderr)
        return 2
    namespace = naming.namespace_for_pr(args.repo, args.pr)
    url = f"https://{render_mod.preview_host(args.pr, args.domain)}"
    body = comment_mod.build_comment(namespace=namespace, url=url, sha=args.sha)
    client = comment_mod.GitHubClient(token, args.repo)
    result = client.upsert_comment(args.pr, body)
    print(f"comment {result} on {args.repo}#{args.pr}")
    return 0


def _cmd_teardown(args) -> int:
    namespace = naming.namespace_for_pr(args.repo, args.pr)
    if args.dry_run:
        print(f"would delete namespace {namespace}")
        return 0
    kube.delete_namespace(namespace)
    print(f"deleted namespace {namespace}")
    return 0


def _cmd_cleanup(args) -> int:
    ttl = cleanup_mod.parse_ttl(args.ttl)
    now = datetime.now(timezone.utc)
    envs = kube.list_preview_environments()
    expired = cleanup_mod.expired_environments(envs, ttl, now)
    for env in expired:
        if args.dry_run:
            print(f"would delete {env.namespace} (pr={env.pr})")
        else:
            kube.delete_namespace(env.namespace)
            print(f"deleted {env.namespace} (pr={env.pr})")
    if not expired:
        print("nothing to clean up")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="previewctl",
        description="Manage per-PR Kubernetes preview environments.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_pr_target(p):
        p.add_argument("--repo", required=True, help="owner/name")
        p.add_argument("--pr", required=True, type=int, help="pull request number")

    p = sub.add_parser("name", help="print the preview namespace for a PR")
    add_pr_target(p)
    p.set_defaults(func=_cmd_name)

    p = sub.add_parser("render", help="write the per-PR Kustomize overlay")
    add_pr_target(p)
    p.add_argument("--image", required=True, help="image name as used in the base")
    p.add_argument("--tag", help="image tag (default: pr-<N>)")
    p.add_argument("--base", default="../../base", help="path to the Kustomize base")
    p.add_argument("--domain", default=DEFAULT_DOMAIN, help="preview domain")
    p.add_argument("--out", help="output directory (default: rendered/pr-<N>)")
    p.set_defaults(func=_cmd_render)

    p = sub.add_parser("comment", help="upsert the preview URL comment on the PR")
    add_pr_target(p)
    p.add_argument("--sha", required=True, help="commit SHA being previewed")
    p.add_argument("--domain", default=DEFAULT_DOMAIN, help="preview domain")
    p.set_defaults(func=_cmd_comment)

    p = sub.add_parser("teardown", help="delete the PR's preview namespace")
    add_pr_target(p)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_cmd_teardown)

    p = sub.add_parser("cleanup", help="delete expired preview namespaces")
    p.add_argument("--ttl", default="24h", help="e.g. 30m, 24h, 7d")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_cmd_cleanup)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
