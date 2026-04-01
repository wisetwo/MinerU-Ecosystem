"""finreport-mcp command-line interface."""

import sys
import argparse

from . import server
from .banner import print_banner


def main() -> None:
    """Entry point for the finreport-mcp command-line interface."""
    parser = argparse.ArgumentParser(
        description="finreport-mcp — MCP server for navigating MinerU-parsed financial reports"
    )

    parser.add_argument(
        "--transport",
        "-t",
        type=str,
        default="stdio",
        help="Transport protocol (default: stdio; options: sse, streamable-http)",
    )

    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=8002,
        help="Server port (default: 8002; only used for HTTP transports)",
    )

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0; only used for HTTP transports)",
    )

    parser.add_argument(
        "--cache-size",
        type=int,
        default=10,
        help="Maximum number of reports to keep in the LRU cache (default: 10)",
    )

    args = parser.parse_args()

    # Warn when host/port are specified but have no effect
    if args.transport == "stdio" and (args.host != "0.0.0.0" or args.port != 8002):
        print(
            "Warning: --host and --port are ignored in stdio mode.",
            file=sys.stderr,
        )

    # Print startup banner — always to stderr so stdio MCP wire is never touched
    host_display = (
        f"{args.host}:{args.port}"
        if args.transport in ("sse", "streamable-http")
        else ""
    )
    print_banner(
        transport=args.transport,
        host=host_display,
        cache_size=args.cache_size,
    )

    server.run_server(
        mode=args.transport,
        port=args.port,
        host=args.host,
        cache_size=args.cache_size,
    )


if __name__ == "__main__":
    main()
