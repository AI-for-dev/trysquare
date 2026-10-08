# SPDX-License-Identifier: BSD-3-Clause
"""The provider's key, added to the agent's requests outside the agent's reach.

An agent calls its model with the key in a header, so a key handed to the sandbox is a
key the agent can read: in its environment, in its home, in a test it writes to print
it. Here the sandbox holds a **placeholder** instead, the agent's provider points at
this relay, and the relay swaps the placeholder for the key on the way out to the one
provider it serves.

Plain HTTP between the agent and the relay, HTTPS from the relay to the provider: the
agent never holds the key, so there is no TLS to break into and no certificate to plant
in an image. The placeholder is what lets a request through, so whoever else on the
machine finds the port has nothing to send. Inside the sandbox it is readable, and it
is worth this relay, for this launch, towards this provider - which is the point.

The relay reaches the provider the way any program here does, through the proxy the
environment names if it names one.
"""

from __future__ import annotations

import http.client
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Headers that describe one connection rather than the request, so they are never
#: forwarded either way. `Host` goes too: the provider's is not the relay's.
HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

#: How long the provider may stay silent on one read. A model thinking before its first
#: token is silent, so this is long; a run's own timeout is what bounds the whole call.
SILENCE = 600


class Relay:
    """One provider's origin, its secrets by placeholder, and a port to reach it on."""

    def __init__(self, origin: str, secrets: dict[str, str], bind: str = "127.0.0.1") -> None:
        self.origin = origin.rstrip("/")
        self.secrets = dict(secrets)
        self._server = ThreadingHTTPServer((bind, 0), _Forward)
        self._server.daemon_threads = True
        self._server.relay = self
        self.port = self._server.server_address[1]
        threading.Thread(
            target=self._server.serve_forever, daemon=True, name="trysquare-relay"
        ).start()

    def reveal(self, text: str) -> tuple[str, bool]:
        """`text` with every placeholder replaced by its secret, and whether one was there."""
        found = False
        for placeholder, secret in self.secrets.items():
            if placeholder in text:
                text, found = text.replace(placeholder, secret), True
        return text, found

    def conceal(self, data: bytes) -> bytes:
        """`data` with every secret replaced by its placeholder."""
        for placeholder, secret in self.secrets.items():
            data = data.replace(secret.encode(), placeholder.encode())
        return data

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _Forward(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_) -> None:
        pass

    def forward(self) -> None:
        relay: Relay = self.server.relay
        path, seen = relay.reveal(self.path)
        headers = {}
        for name, value in self.headers.items():
            if name.lower() not in HOP:
                headers[name], found = relay.reveal(value)
                seen = seen or found
        if not seen:
            self.send_error(403, "this relay serves the sandboxed agent only")
            return
        request = urllib.request.Request(
            relay.origin + path, data=self.body(), headers=headers, method=self.command
        )
        try:
            upstream = urllib.request.urlopen(request, timeout=SILENCE)
        except urllib.error.HTTPError as refused:
            upstream = refused
        except (urllib.error.URLError, OSError) as e:
            self.send_error(502, f"the provider is out of reach: {e}")
            return
        with upstream:
            self.answer(upstream, relay)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = forward

    def body(self) -> bytes | None:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            data = b""
            while size := int(self.rfile.readline().split(b";")[0], 16):
                data += self.rfile.read(size)
                self.rfile.readline()
            self.rfile.readline()
            return data
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else None

    def answer(self, upstream, relay: Relay) -> None:
        """The provider's answer, passed on as it arrives.

        A refusal is read whole and stripped of the secrets first: a provider that quotes
        the key back in its error would otherwise hand it to the agent. A success is
        streamed chunk by chunk, since an agent reads its tokens as they come. When either
        side hangs up mid-stream, the agent's connection is dropped without the final
        chunk, so a truncated answer never passes for a complete one.
        """
        status = upstream.status
        self.send_response(status)
        for name, value in upstream.headers.items():
            if name.lower() not in HOP:
                self.send_header(name, value)
        if status >= 400:
            data = relay.conceal(upstream.read())
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while chunk := upstream.read1(65536):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
        except (http.client.HTTPException, OSError):
            self.close_connection = True
            return
        self.wfile.write(b"0\r\n\r\n")
