"""Command-line entry point for the headless CAN-X runtime."""

import argparse
from collections.abc import Sequence

import uvicorn

from canx.api.app import create_app


def build_parser() -> argparse.ArgumentParser:
    """Build the runtime command-line parser."""
    parser = argparse.ArgumentParser(prog="canx-runtime")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--session-token")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the CAN-X API without a desktop process."""
    arguments = build_parser().parse_args(argv)
    server: uvicorn.Server | None = None

    def request_shutdown() -> None:
        if server is not None:
            server.should_exit = True

    app = create_app(
        session_token=arguments.session_token,
        shutdown_callback=request_shutdown if arguments.session_token is not None else None,
    )
    server = uvicorn.Server(uvicorn.Config(app, host=arguments.host, port=arguments.port))
    server.run()


if __name__ == "__main__":
    main()
