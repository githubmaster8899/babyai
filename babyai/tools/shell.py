import glob
import os
from pathlib import Path

from babyai.tools.base import Tool, ToolResult
from babyai.safety.rules import RiskLevel


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a file from disk."
    risk_level = RiskLevel.LOW

    def invoke(self, path: str, offset: int = 0, limit: int | None = None) -> ToolResult:
        try:
            data = Path(path).read_text(encoding="utf-8")
            lines = data.splitlines()
            if offset:
                lines = lines[offset:]
            if limit is not None:
                lines = lines[:limit]
            return ToolResult(True, "\n".join(lines), {"path": path})
        except Exception as exc:
            return ToolResult(False, f"Failed to read {path}: {exc}")


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write or create a file on disk."
    risk_level = RiskLevel.MEDIUM

    def invoke(self, path: str, content: str) -> ToolResult:
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return ToolResult(True, f"Wrote file: {path}", {"path": path})
        except Exception as exc:
            return ToolResult(False, f"Failed to write {path}: {exc}")


class EditFileTool(Tool):
    name = "edit_file"
    description = "Replace a string in an existing file."
    risk_level = RiskLevel.MEDIUM

    def invoke(self, path: str, old: str, new: str) -> ToolResult:
        try:
            target = Path(path)
            text = target.read_text(encoding="utf-8")
            if old not in text:
                return ToolResult(False, f"Pattern not found in {path}")
            updated = text.replace(old, new)
            target.write_text(updated, encoding="utf-8")
            return ToolResult(True, f"Updated file: {path}", {"path": path})
        except Exception as exc:
            return ToolResult(False, f"Failed to edit {path}: {exc}")


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = "List the contents of a directory."
    risk_level = RiskLevel.LOW

    def invoke(self, path: str = ".") -> ToolResult:
        try:
            entries = sorted(os.listdir(path))
            return ToolResult(True, "\n".join(entries), {"path": path, "entries": entries})
        except Exception as exc:
            return ToolResult(False, f"Failed to list {path}: {exc}")


class FindFilesTool(Tool):
    name = "find_files"
    description = "Find files matching a glob pattern."
    risk_level = RiskLevel.LOW

    def invoke(self, pattern: str, root: str = ".") -> ToolResult:
        try:
            matches = sorted(glob.glob(os.path.join(root, pattern), recursive=True))
            return ToolResult(True, "\n".join(matches), {"pattern": pattern, "root": root, "matches": matches})
        except Exception as exc:
            return ToolResult(False, f"Failed to find files: {exc}")


class SearchFilesTool(Tool):
    name = "search_files"
    description = "Search text content inside files using a keyword or regex-like pattern."
    risk_level = RiskLevel.LOW

    def invoke(self, query: str, root: str = ".", file_pattern: str = "*") -> ToolResult:
        try:
            matches: list[str] = []
            for path in Path(root).rglob(file_pattern):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except Exception:
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if query.lower() in line.lower():
                        matches.append(f"{path}:{line_number}:{line.strip()}")
            return ToolResult(True, "\n".join(matches[:100]), {"query": query, "matches": matches[:100]})
        except Exception as exc:
            return ToolResult(False, f"Failed to search files: {exc}")
