import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from previewctl.comment import MARKER, GitHubClient, GitHubError, build_comment


class TestBuildComment:
    def test_contains_marker_url_namespace_and_short_sha(self):
        body = build_comment(
            namespace="preview-app-pr-12",
            url="https://pr-12.preview.example.com",
            sha="0123456789abcdef",
        )
        assert MARKER in body
        assert "https://pr-12.preview.example.com" in body
        assert "preview-app-pr-12" in body
        assert "0123456789ab" in body
        assert "0123456789abcdef" not in body  # sha is shortened

    def test_rejects_relative_url(self):
        with pytest.raises(ValueError):
            build_comment(namespace="ns", url="/pr-12", sha="abc")

    def test_rejects_empty_namespace(self):
        with pytest.raises(ValueError):
            build_comment(namespace="", url="https://x.example.com", sha="abc")


class FakeClient(GitHubClient):
    """GitHubClient with the HTTP layer replaced by a call log."""

    def __init__(self, comments):
        super().__init__("token", "octocat/hello-world")
        self._comments = comments
        self.calls = []

    def _request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET":
            return list(self._comments)
        return {"id": 1}


class TestUpsertComment:
    def test_creates_when_no_marker_comment_exists(self):
        client = FakeClient([{"id": 5, "body": "a human comment"}])
        result = client.upsert_comment(12, f"{MARKER}\nbody")
        assert result == "created"
        method, path, payload = client.calls[-1]
        assert method == "POST"
        assert path == "/repos/octocat/hello-world/issues/12/comments"
        assert payload == {"body": f"{MARKER}\nbody"}

    def test_updates_existing_marker_comment(self):
        client = FakeClient(
            [
                {"id": 5, "body": "a human comment"},
                {"id": 9, "body": f"{MARKER}\nstale preview info"},
            ]
        )
        result = client.upsert_comment(12, f"{MARKER}\nfresh")
        assert result == "updated"
        method, path, payload = client.calls[-1]
        assert method == "PATCH"
        assert path == "/repos/octocat/hello-world/issues/comments/9"
        assert payload == {"body": f"{MARKER}\nfresh"}

    def test_only_one_write_per_upsert(self):
        client = FakeClient([])
        client.upsert_comment(1, "x")
        assert len(client.calls) == 2  # GET + one write


class TestClientValidation:
    def test_requires_token(self):
        with pytest.raises(ValueError):
            GitHubClient("", "a/b")


@pytest.fixture
def github_stub():
    """A local HTTP server answering every request with a canned error."""

    class Handler(BaseHTTPRequestHandler):
        status = 404
        body = json.dumps({"message": "Not Found"}).encode()

        def do_GET(self):  # noqa: N802 - stdlib handler API
            self.send_response(self.status)
            self.send_header("Content-Length", str(len(self.body)))
            self.end_headers()
            self.wfile.write(self.body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Handler, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


class TestRequestErrors:
    def test_http_error_becomes_github_error_with_message(self, github_stub):
        _, url = github_stub
        client = GitHubClient("t", "a/b", base_url=url)
        with pytest.raises(GitHubError) as excinfo:
            client.list_comments(1)
        assert str(excinfo.value) == (
            "GitHub API GET /repos/a/b/issues/1/comments failed: HTTP 404 Not Found"
        )

    def test_non_json_error_body_is_kept(self, github_stub):
        handler, url = github_stub
        handler.status, handler.body = 502, b"bad gateway"
        client = GitHubClient("t", "a/b", base_url=url)
        with pytest.raises(GitHubError, match="HTTP 502 bad gateway"):
            client.list_comments(1)

    def test_unreachable_host_becomes_github_error(self):
        # Port 9 (discard) on localhost: nothing listens there in CI.
        client = GitHubClient("t", "a/b", base_url="http://127.0.0.1:9")
        with pytest.raises(GitHubError, match="cannot reach http://127.0.0.1:9"):
            client.list_comments(1)
