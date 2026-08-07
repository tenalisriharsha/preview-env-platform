"""Tiny stdlib-only HTTP server used as the preview workload.

It exists so a preview environment has something real to serve: the response
identifies which PR build is running, which is all a preview needs to prove.
Configuration comes from environment variables so the same image works for
every PR without rebuilds beyond the tag.
"""

import os
from http.server import BaseHTTPRequestHandler, HTTPServer

MESSAGE = os.environ.get("APP_MESSAGE", "hello from preview-env-platform")
VERSION = os.environ.get("APP_VERSION", "dev")


class Handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - stdlib handler API
        if self.path == "/healthz":
            self._respond(200, "ok\n")
        else:
            self._respond(200, f"{MESSAGE} (version={VERSION})\n")

    def log_message(self, *args):  # keep container logs quiet
        pass


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
