"""Servidor HTTP local para testar o download sem acessar a rede."""

from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class FakeServer:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fail_get: dict[str, int] = {}  # caminho -> quantas respostas 503 antes de servir
        self.fail_status = 503  # status usado nas falhas de fail_get
        self.empty_get: dict[str, int] = {}  # caminho -> quantas respostas 200 vazias antes de servir
        self.html_get: dict[str, int] = {}  # caminho -> quantas paginas HTML de recusa (200) antes de servir
        self.require_auth: str | None = None  # "usuario:senha" exigido via HTTP basic
        self.honor_range = True
        self.range_requests: list[str] = []
        self.httpd: ThreadingHTTPServer | None = None
        self.base_url = ""

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path}"


@pytest.fixture
def server():
    state = FakeServer()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silencia o log do servidor
            pass

        def _body(self) -> bytes | None:
            return state.files.get(self.path.lstrip("/"))

        def _authorized(self) -> bool:
            if state.require_auth is None:
                return True
            expected = "Basic " + base64.b64encode(state.require_auth.encode()).decode()
            if self.headers.get("Authorization") == expected:
                return True
            self.send_response(401)
            self.end_headers()
            return False

        def do_HEAD(self):
            if not self._authorized():
                return
            body = self._body()
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

        def do_GET(self):
            if not self._authorized():
                return
            key = self.path.lstrip("/")
            body = self._body()
            if body is None:
                self.send_response(404)
                self.end_headers()
                return

            if state.fail_get.get(key, 0) > 0:
                state.fail_get[key] -= 1
                self.send_response(state.fail_status)
                self.end_headers()
                return

            if state.empty_get.get(key, 0) > 0:
                state.empty_get[key] -= 1
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if state.html_get.get(key, 0) > 0:
                state.html_get[key] -= 1
                page = (
                    b"<html><head><title>Request Rejected</title></head><body>The requested URL was rejected."
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return

            range_header = self.headers.get("Range")
            if range_header and state.honor_range:
                state.range_requests.append(range_header)
                start = int(range_header.removeprefix("bytes=").split("-")[0])
                if start >= len(body):
                    self.send_response(416)
                    self.end_headers()
                    return
                chunk = body[start:]
                self.send_response(206)
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.end_headers()
                self.wfile.write(chunk)
                return

            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.httpd = httpd
    state.base_url = f"http://127.0.0.1:{httpd.server_port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
