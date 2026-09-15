"""icon-mcp-server: a Model Context Protocol server that generates software icons."""

from .generator import (
    IconRequest,
    IconResult,
    generate_icon,
    generate_icon_set,
    list_styles,
)

__version__ = "0.1.0"

__all__ = [
    "IconRequest",
    "IconResult",
    "generate_icon",
    "generate_icon_set",
    "list_styles",
    "__version__",
]
