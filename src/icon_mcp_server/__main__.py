"""Allow ``python -m icon_mcp_server``."""

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
