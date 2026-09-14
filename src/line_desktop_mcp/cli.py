"""Command entry point for uvx/pipx (single responsibility: argument parsing)."""

import argparse

from .server import mcp


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser (kept separate for testability)."""
    parser = argparse.ArgumentParser(prog="line-desktop-mcp")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http"],
        help="stdio for desktop MCP clients, http for remote access",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host only")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port only")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the MCP server (installed as `line-desktop-mcp` console script)."""
    args = build_parser().parse_args(argv)

    # Default stays stdio so desktop clients connect without extra flags.
    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
