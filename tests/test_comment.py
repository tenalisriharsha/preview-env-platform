import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

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


@pytest.fixture
def paginated_github_stub():
    """A local HTTP server that spreads comments over two pages.

    Page 1 holds 30 "noise" comments with no marker and a ``Link`` header
    pointing at page 2. Page 2 holds the bot's marker comment. A client
    that reads only the first page (the documented bug) will never see it.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            page = query.get("page", ["1"])[0]
            if page == "1":
                comments = [{"id": i, "body": "a human comment"} for i in range(1, 31)]
                body = json.dumps(comments).encode()
                next_url = (
                    f"http://127.0.0.1:{self.server.server_port}"
                    "/repos/a/b/issues/1/comments?page=2"
                )
                link_header = f'<{next_url}>; rel="next", <{next_url}>; rel="last"'
            else:
                comments = [{"id": 999, "body": f"{MARKER}\nstale preview info"}]
                body = json.dumps(comments).encode()
                link_header = None
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            if link_header:
                self.send_header("Link", link_header)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self._write_json({"id": 1000})

        def do_PATCH(self):
            self._write_json({"id": 999})

        def _write_json(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


class TestListCommentsPagination:
    def test_list_comments_includes_second_page(self, paginated_github_stub):
        client = GitHubClient("t", "a/b", base_url=paginated_github_stub)
        comments = client.list_comments(1)
        assert len(comments) == 31
        assert any(c["id"] == 999 for c in comments)

    def test_upsert_updates_marker_comment_found_on_page_two(self, paginated_github_stub):
        client = GitHubClient("t", "a/b", base_url=paginated_github_stub)
        result = client.upsert_comment(1, f"{MARKER}\nfresh")
        assert result == "updated"


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


class TestRequestTimeout:
    def test_unresponsive_server_does_not_block_forever(self):
        # A server that accepts the connection but never writes a response
        # (network partition, GitHub outage) must not be able to block the
        # caller indefinitely. Nothing here ever closes the connection on
        # its own, so a passing test proves _request enforces its own
        # timeout rather than happening to outrun some fixed delay.
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        accepted = []

        def accept_and_hang():
            try:
                conn, _ = server.accept()
                accepted.append(conn)
            except OSError:
                pass

        accept_thread = threading.Thread(target=accept_and_hang, daemon=True)
        accept_thread.start()

        client = GitHubClient("t", "a/b", base_url=f"http://127.0.0.1:{port}")
        result = {}

        def call():
            try:
                client.list_comments(1)
                result["status"] = "ok"
            except GitHubError:
                result["status"] = "error"
            except Exception as exc:  # noqa: BLE001 - diagnostic only, see assert below
                # Anything other than GitHubError means a raw timeout
                # exception leaked out instead of being translated, or (if
                # the thread is still "hung" when the main thread closes the
                # socket below) a connection-reset artifact of that cleanup.
                result["status"] = "crashed"
                result["exc"] = repr(exc)

        call_thread = threading.Thread(target=call, daemon=True)
        call_thread.start()
        call_thread.join(timeout=15)
        hung = call_thread.is_alive()

        server.close()
        for conn in accepted:
            conn.close()

        assert not hung, (
            "GitHubClient._request passed no timeout to urlopen, so it "
            "blocked indefinitely on an unresponsive server instead of "
            "failing fast"
        )
        assert result.get("status") == "error", (
            f"expected a GitHubError, got {result!r} instead — a timeout "
            "must be translated into GitHubError, not left to leak out raw"
        )
