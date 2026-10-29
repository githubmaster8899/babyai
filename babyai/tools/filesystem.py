import glob
import os
from pathlib import Path

from babyai.tools.base import Tool, ToolResult


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read file contents from disk."

    def invoke(self, path: str, offset: int = 0, limit: int | None = None) -> ToolResult:
        try:
            text = Path(path).read_text(encoding="utf-8")
            lines = text.splitlines()
            if offset:
                lines = lines[offset:]
            if limit is not None:
                lines = lines[:limit]
            return ToolResult(True, "\n".join(lines), {"path": path})
        except Exception as exc:
            return ToolResult(False, f"Failed to read file: {exc}")


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a file on disk."

    def invoke(self, path: str, content: str) -> ToolResult:
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return ToolResult(True, f"Wrote file: {path}", {"path": path})
        except Exception as exc:
            return ToolResult(False, f"Failed to write file: {exc}")


class EditFileTool(Tool):
    name = "edit_file"
    description = "Replace text in an existing file."

    def invoke(self, path: str, old: str, new: str) -> ToolResult:
        try:
            target = Path(path)
            current = target.read_text(encoding="utf-8")
            if old not in current:
                return ToolResult(False, f"Pattern not found in '{path}'.")
            updated = current.replace(old, new)
            target.write_text(updated, encoding="utf-8")
            return ToolResult(True, f"Updated file: {path}", {"path": path})
        except Exception as exc:
            return ToolResult(False, f"Failed to edit file: {exc}")


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = "List files and directories in a directory."

    def invoke(self, path: str = ".") -> ToolResult:
        try:
            entries = sorted(os.listdir(path))
            return ToolResult(True, "\n".join(entries), {"path": path, "entries": entries})
        except Exception as exc:
            return ToolResult(False, f"Failed to list directory: {exc}")


class FindFilesTool(Tool):
    name = "find_files"
    description = "Find files matching a glob pattern."

    def invoke(self, pattern: str, root: str = ".") -> ToolResult:
        try:
            matches = sorted(glob.glob(os.path.join(root, pattern), recursive=True))
            return ToolResult(True, "\n".join(matches), {"pattern": pattern, "matches": matches})
        except Exception as exc:
            return ToolResult(False, f"Failed to find files: {exc}")


class SearchFilesTool(Tool):
    name = "search_files"
    description = "Search the contents of files for a keyword."

    def invoke(self, query: str, root: str = ".", file_pattern: str = "*") -> ToolResult:
        matches: list[str] = []
        try:
            for path in Path(root).rglob(file_pattern):
                if not path.is_file():
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except Exception:
                    continue
                for lineno, line in enumerate(content.splitlines(), start=1):
                    if query.lower() in line.lower():
                        matches.append(f"{path}:{lineno}:{line.strip()}")
            return ToolResult(True, "\n".join(matches[:100]), {"query": query, "matches": matches[:100]})
        except Exception as exc:
            return ToolResult(False, f"Failed to search files: {exc}")
