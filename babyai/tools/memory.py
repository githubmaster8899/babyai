from babyai.tools.base import Tool, ToolResult


class RememberFactTool(Tool):
    name = "remember_fact"
    description = "Store a fact in the long-term memory database."

    def invoke(self, fact: str, source: str = "user") -> ToolResult:
        if not self.memory_store:
            return ToolResult(False, "Memory store is not configured.")
        result = self.memory_store.remember_fact(fact, source)
        return ToolResult(True, f"Remembered: {fact}", result)


class RecallMemoryTool(Tool):
    name = "recall_memory"
    description = "Search memory for facts and previous conversations."

    def invoke(self, query: str, limit: int = 5) -> ToolResult:
        if not self.memory_store:
            return ToolResult(False, "Memory store is not configured.")
        results = self.memory_store.search_memory(query, limit=limit)
        return ToolResult(True, str(results), {"results": results})


class ForgetMemoryTool(Tool):
    name = "forget_memory"
    description = "Delete relevant memory entries by query."

    def invoke(self, query: str) -> ToolResult:
        if not self.memory_store:
            return ToolResult(False, "Memory store is not configured.")
        deleted = self.memory_store.forget_memory(query=query)
        return ToolResult(True, f"Removed {deleted} memory entries.", {"deleted": deleted})
