import sys

if sys.version_info < (3, 10):
    raise RuntimeError(
        "Garmin MCP server requires Python 3.10+. "
        f"Current version: {sys.version.split()[0]}"
    )

from mcp_server.garmin_server import main


if __name__ == "__main__":
    main()
