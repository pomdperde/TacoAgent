"""Tool interfaces and registration for TacoAgent.

Future tools should live in this package and be added through
``build_tool_registry`` rather than imported directly by the agent.
"""

from .audit import ToolExecutionLogger
from .base import Tool
from .command_run import CommandRunTool
from .file_create import FileCreateTool
from .file_delete import FileDeleteTool
from .file_edit import FileEditTool, NonUniqueTextError
from .file_read import FileReadTool
from .memory_get import MemoryGetTool
from .factory import build_structured_tools, create_tools
from .registry import ToolRegistry, build_tool_registry
from .regex_search import RegexSearchTool
from .workspace import ListWorkspaceFilesTool
from .web_search_tool import WebSearchTool

__all__ = [
    "ListWorkspaceFilesTool",
    "FileCreateTool",
    "FileDeleteTool",
    "CommandRunTool",
    "FileEditTool",
    "FileReadTool",
    "MemoryGetTool",
    "Tool",
    "NonUniqueTextError",
    "RegexSearchTool",
    "ToolExecutionLogger",
    "ToolRegistry",
    "build_tool_registry",
    "build_structured_tools",
    "create_tools",
    "WebSearchTool",
]
