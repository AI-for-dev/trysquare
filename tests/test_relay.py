# SPDX-License-Identifier: BSD-3-Clause
"""The relay that adds the provider's key to the agent's requests, from outside its reach.

Nothing here reaches a provider: the upstream is a local server that records what it was
sent and answers what it is told to.
"""

import http.client
import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from trysquare.relay import Relay

PLACEHOLDER = "trysquare-KEY-0123456789abcdef"
SECRET = "sk-the-real-key"


class Upstream:
    """A provider stand-in: records each request, answers with `status` and `chunks`,
    and hangs up before the end of its answer if `cut`."""

    def __init__(
        self,
        status: int = 200,
        chunks: list[bytes] = (b"ok",),
        pause: float = 0,
        cut: bool = False,
    ):
        self.seen: list[dict] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                upstream.seen.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": self.rfile.read(length),
                    }
                )
                self.send_response(status)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for chunk in chunks:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                    time.sleep(pause)
                if cut:
                    self.close_connection = True
                    return
                self.wfile.write(b"0\r\n\r\n")

            do_GET = do_POST

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstream():
    made = []

    def make(**kwargs) -> Upstream:
        made.append(Upstream(**kwargs))
        return made[-1]

    yield make
    for server in made:
        server.close()


def relayed(upstream: Upstream) -> Relay:
    return Relay(upstream.origin, {PLACEHOLDER: SECRET})


@contextmanager
def handled():
    """Waits, on the way out, for the connections opened inside to be handled."""
    before = set(threading.enumerate())
    yield
    for thread in set(threading.enumerate()) - before:
        thread.join(timeout=10)


def post(relay: Relay, path: str = "/v1/chat", key: str = PLACEHOLDER, body: bytes = b"{}"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{relay.port}{path}",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=10)


class TestTheKeyIsAddedOutside:
    def test_the_placeholder_becomes_the_key_on_the_way_out(self, upstream):
        server = upstream()
        with post(relayed(server)) as answer:
            assert answer.read() == b"ok"
        assert server.seen[0]["headers"]["Authorization"] == f"Bearer {SECRET}"

    def test_the_request_reaches_the_provider_as_the_agent_wrote_it(self, upstream):
        server = upstream()
        post(relayed(server), "/v1/chat/completions?x=1", body=b'{"model": "m"}').read()
        assert server.seen[0]["path"] == "/v1/chat/completions?x=1"
        assert json.loads(server.seen[0]["body"]) == {"model": "m"}

    def test_a_request_without_the_placeholder_is_refused(self, upstream):
        """Anyone else on the machine who finds the port has no placeholder to send."""
        server = upstream()
        with pytest.raises(urllib.error.HTTPError) as refused:
            post(relayed(server), key="anything")
        assert refused.value.code == 403
        assert server.seen == []


class TestWhatComesBack:
    def test_a_stream_is_passed_on_as_it_arrives(self, upstream):
        """An agent reads its tokens as they come; a relay that buffered would hold a
        whole answer back, and every duration measured through it would be wrong."""
        server = upstream(chunks=[b"data: 1\n\n", b"data: 2\n\n"], pause=1.5)
        start = time.monotonic()
        with post(relayed(server)) as answer:
            first = answer.read1()
            arrived = time.monotonic() - start
            assert first == b"data: 1\n\n"
            assert answer.read() == b"data: 2\n\n"
        assert arrived < 1.0

    def test_a_stream_cut_by_the_provider_is_cut_for_the_agent(self, upstream, capsys):
        """A truncated answer must not reach the agent as a complete one, and a provider
        that hangs up is not a fault of the relay to report with a traceback."""
        server = upstream(chunks=[b"data: 1\n\n"], cut=True)
        with post(relayed(server)) as answer:
            with pytest.raises(http.client.IncompleteRead) as cut:
                answer.read()
        assert cut.value.partial == b"data: 1\n\n"
        assert capsys.readouterr().err == ""

    def test_a_refusal_from_the_provider_is_passed_on_without_the_key(self, upstream):
        """A provider that quotes the key back in its error would hand it to the agent."""
        server = upstream(status=401, chunks=[f"bad key {SECRET}".encode()])
        with pytest.raises(urllib.error.HTTPError) as refused:
            post(relayed(server))
        assert refused.value.code == 401
        assert refused.value.read() == f"bad key {PLACEHOLDER}".encode()

    def test_a_provider_out_of_reach_is_a_bad_gateway(self):
        relay = Relay("http://127.0.0.1:9", {PLACEHOLDER: SECRET})
        with pytest.raises(urllib.error.HTTPError) as failed:
            post(relay)
        assert failed.value.code == 502


class TestTheAgentsSide:
    def test_an_idle_connection_reset_by_the_agent_is_not_reported(self, upstream, capsys):
        """A container drops a keep-alive connection while the relay waits for its next
        request: no request failed, so nothing reaches the operator's terminal."""
        relay = relayed(upstream())
        with handled():
            agent = http.client.HTTPConnection("127.0.0.1", relay.port, timeout=10)
            agent.request("POST", "/v1/chat", b"{}", {"Authorization": f"Bearer {PLACEHOLDER}"})
            assert agent.getresponse().read() == b"ok"
            agent.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            agent.close()
        assert capsys.readouterr().err == ""

    def test_a_request_the_relay_cannot_read_is_still_reported(self, upstream, capsys):
        relay = relayed(upstream())
        with socket.create_connection(("127.0.0.1", relay.port), timeout=10) as agent:
            agent.sendall(
                b"POST /v1/chat HTTP/1.1\r\nAuthorization: Bearer %s\r\n"
                b"Transfer-Encoding: chunked\r\n\r\nnot-hex\r\n" % PLACEHOLDER.encode()
            )
            # The relay hangs up once it has reported the request it could not read.
            assert agent.recv(1) == b""
        assert "ValueError" in capsys.readouterr().err
