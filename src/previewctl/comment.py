"""PR comment templating and the GitHub API upsert client.

The bot maintains exactly one comment per PR: it locates a previous comment
of its own via the ``MARKER`` HTML comment and updates it in place, creating
a new one only when none exists.
"""

import json
import re
import urllib.error
import urllib.request

_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="([^"]+)"')

MARKER = "<!-- preview-env -->"

COMMENT_TEMPLATE = """\
{marker}
### Preview environment

| | |
|---|---|
| **URL** | {url} |
| **Namespace** | `{namespace}` |
| **Commit** | `{sha}` |

This environment is temporary — it is torn down when the PR is closed,
and expires automatically after the configured TTL.\
"""


def build_comment(*, namespace: str, url: str, sha: str) -> str:
    """Return the markdown body of the preview comment."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must be absolute")
    if not namespace:
        raise ValueError("namespace must be non-empty")
    return COMMENT_TEMPLATE.format(
        marker=MARKER, url=url, namespace=namespace, sha=sha[:12]
    )


class GitHubError(RuntimeError):
    """A GitHub API call failed or GitHub could not be reached."""


class GitHubClient:
    """Minimal GitHub REST client (stdlib only).

    ``base_url`` is injectable so tests can point the client at a stub.
    """

    def __init__(self, token: str, repo: str, base_url: str = "https://api.github.com"):
        if not token:
            raise ValueError("token must be non-empty")
        self._token = token
        self.repo = repo
        self.base_url = base_url.rstrip("/")
        self._last_response_headers = None

    def _request(self, method: str, path: str, payload: dict | None = None):
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                self._last_response_headers = resp.headers
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("message", detail)
            except (ValueError, AttributeError):
                pass
            raise GitHubError(
                f"GitHub API {method} {path} failed: HTTP {exc.code} {detail}".rstrip()
            ) from None
        except urllib.error.URLError as exc:
            raise GitHubError(f"cannot reach {self.base_url}: {exc.reason}") from None
        return json.loads(raw) if raw else None

    def list_comments(self, pr_number: int) -> list[dict]:
        path = f"/repos/{self.repo}/issues/{pr_number}/comments"
        comments = []
        while path:
            comments.extend(self._request("GET", path))
            path = self._next_page_path()
        return comments

    def _next_page_path(self) -> str | None:
        """Return the path of the next page per the last response's ``Link`` header."""
        link = self._last_response_headers.get("Link") if self._last_response_headers else None
        if not link:
            return None
        for url, rel in _LINK_RE.findall(link):
            if rel == "next":
                return url[len(self.base_url) :] if url.startswith(self.base_url) else url
        return None

    def create_comment(self, pr_number: int, body: str) -> dict:
        return self._request(
            "POST", f"/repos/{self.repo}/issues/{pr_number}/comments", {"body": body}
        )

    def update_comment(self, comment_id: int, body: str) -> dict:
        return self._request(
            "PATCH", f"/repos/{self.repo}/issues/comments/{comment_id}", {"body": body}
        )

    def upsert_comment(self, pr_number: int, body: str) -> str:
        """Create or update the preview comment. Returns 'created' or 'updated'."""
        for comment in self.list_comments(pr_number):
            if MARKER in comment.get("body", ""):
                self.update_comment(comment["id"], body)
                return "updated"
        self.create_comment(pr_number, body)
        return "created"
