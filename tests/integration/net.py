"""Network helpers for real loopback integration tests."""

import socket


def find_free_port() -> int:
    """Reserve and release a loopback port for an immediate test server bind."""
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])
