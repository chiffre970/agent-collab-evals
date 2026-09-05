#!/usr/bin/env python3
"""Run one command behind a loopback-to-Unix-socket relay with a hard timeout."""

from __future__ import annotations

import argparse
import os
import selectors
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            upstream.connect(str(self.server.broker_socket))  # type: ignore[attr-defined]
            _copy_bidirectionally(self.request, upstream)
        finally:
            upstream.close()


class _RelayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], broker_socket: Path) -> None:
        self.broker_socket = broker_socket
        super().__init__(address, _RelayHandler)


def _copy_bidirectionally(left: socket.socket, right: socket.socket) -> None:
    selector = selectors.DefaultSelector()
    peers = {left: right, right: left}
    try:
        for current in peers:
            current.setblocking(False)
            selector.register(current, selectors.EVENT_READ)
        while selector.get_map():
            for key, _ in selector.select(timeout=30):
                current = key.fileobj
                assert isinstance(current, socket.socket)
                try:
                    data = current.recv(65_536)
                except BlockingIOError:
                    continue
                peer = peers[current]
                if not data:
                    selector.unregister(current)
                    try:
                        peer.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                peer.setblocking(True)
                try:
                    peer.sendall(data)
                finally:
                    peer.setblocking(False)
    finally:
        selector.close()


def _endpoint(value: str, expected_path: str = "/v1") -> tuple[str, int]:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            f"broker endpoint must be fixed IPv4 loopback with path {expected_path}"
        )
    return parsed.hostname, parsed.port


def _broker_socket(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("broker socket path must be absolute")
    try:
        mode = path.stat().st_mode
    except FileNotFoundError as error:
        raise ValueError("broker socket does not exist") from error
    if not stat.S_ISSOCK(mode):
        raise ValueError("broker transport is not a Unix socket")
    return path


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="session-launcher")
    parser.add_argument("--timeout-seconds", type=int, required=True)
    parser.add_argument("--broker-socket", required=True)
    parser.add_argument("--model-endpoint", required=True)
    parser.add_argument("--peer-broker-socket")
    parser.add_argument("--peer-endpoint")
    if "--" not in argv:
        parser.error("command must follow --")
    separator = argv.index("--")
    arguments = parser.parse_args(argv[:separator])
    if arguments.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    if (arguments.peer_broker_socket is None) != (
        arguments.peer_endpoint is None
    ):
        parser.error(
            "--peer-broker-socket and --peer-endpoint must be configured together"
        )
    arguments.command = argv[separator + 1 :]
    if not arguments.command or any(not value for value in arguments.command):
        parser.error("command must be nonempty")
    return arguments


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(list(argv if argv is not None else sys.argv[1:]))
    host, port = _endpoint(arguments.model_endpoint)
    broker = _broker_socket(arguments.broker_socket)
    relay_specs = [("model", host, port, broker)]
    if arguments.peer_endpoint is not None:
        peer_host, peer_port = _endpoint(arguments.peer_endpoint, "/v1/call")
        if (peer_host, peer_port) == (host, port):
            raise ValueError("model and peer relays must use different loopback ports")
        relay_specs.append(
            (
                "peer",
                peer_host,
                peer_port,
                _broker_socket(arguments.peer_broker_socket),
            )
        )
    relays: list[tuple[_RelayServer, threading.Thread]] = []
    try:
        for label, relay_host, relay_port, relay_socket in relay_specs:
            relay = _RelayServer((relay_host, relay_port), relay_socket)
            relay_thread = threading.Thread(
                target=relay.serve_forever,
                name=f"session-{label}-relay",
                daemon=True,
            )
            relay_thread.start()
            relays.append((relay, relay_thread))
        child = subprocess.Popen(arguments.command, start_new_session=True)
    except BaseException:
        _close_relays(relays)
        raise

    def terminate_child(_signal: int, _frame: object) -> None:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)

    previous = {
        signum: signal.signal(signum, terminate_child)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        try:
            return child.wait(timeout=arguments.timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            return 124
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        _close_relays(relays)


def _close_relays(
    relays: list[tuple[_RelayServer, threading.Thread]],
) -> None:
    for relay, relay_thread in reversed(relays):
        relay.shutdown()
        relay.server_close()
        relay_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
