"""DNS-1123-safe naming for preview namespaces.

Every preview environment lives in a namespace named
``preview-<repo>-pr-<N>``; the whole name must be a valid DNS-1123 label
(lowercase alphanumerics and '-', max 63 chars) because it also ends up in
the ingress host.
"""

import re

MAX_LABEL_LENGTH = 63

_NON_LABEL_CHARS = re.compile(r"[^a-z0-9-]+")
_RUNS_OF_DASHES = re.compile(r"-{2,}")


def slugify(value: str) -> str:
    """Reduce an arbitrary string to a DNS-1123-compatible fragment."""
    slug = _NON_LABEL_CHARS.sub("-", value.lower())
    slug = _RUNS_OF_DASHES.sub("-", slug)
    return slug.strip("-")


def repo_name(repo: str) -> str:
    """Extract the repository name from an ``owner/name`` slug."""
    if not repo or not repo.strip("/"):
        raise ValueError("repo must be a non-empty 'owner/name' slug")
    return repo.strip("/").split("/")[-1]


def namespace_for_pr(repo: str, pr_number: int) -> str:
    """Return the preview namespace for a pull request.

    The result is always a valid DNS-1123 label of at most 63 characters;
    long repository names are truncated from the middle of the repo fragment
    so the ``pr-<N>`` suffix (the load-bearing part) is preserved.
    """
    if pr_number < 1:
        raise ValueError("pr_number must be a positive integer")
    name = slugify(repo_name(repo))
    if not name:
        raise ValueError(f"repo {repo!r} has no usable name fragment")
    suffix = f"-pr-{pr_number}"
    budget = MAX_LABEL_LENGTH - len("preview") - 1 - len(suffix)
    if budget < 1:
        raise ValueError(f"pr_number {pr_number} is too large to fit a label")
    return f"preview-{name[:budget].rstrip('-')}{suffix}"
