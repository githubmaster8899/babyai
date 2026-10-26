import re
from urllib import request

from babyai.tools.base import Tool, ToolResult
from babyai.safety.rules import RiskLevel


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for public information."
    risk_level = RiskLevel.LOW

    def invoke(self, query: str, max_results: int = 5) -> ToolResult:
        try:
            q = query.replace(" ", "+")
            url = f"https://duckduckgo.com/html/?q={q}"
            with request.urlopen(url, timeout=15) as response:
                html = response.read().decode("utf-8", errors="ignore")
            matches = re.findall(r'<a rel="nofollow" class="result-link" href="(.*?)">(.*?)</a>', html)
            items = [
                {"url": match[0], "title": re.sub(r'<.*?>', '', match[1])}
                for match in matches[:max_results]
            ]
            return ToolResult(True, str(items), {"results": items})
        except Exception as exc:
            return ToolResult(False, f"Web search failed: {exc}")


class FetchURLTool(Tool):
    name = "fetch_url"
    description = "Fetch and extract the main text from a URL."
    risk_level = RiskLevel.LOW

    def invoke(self, url: str) -> ToolResult:
        try:
            with request.urlopen(url, timeout=15) as response:
                html = response.read().decode("utf-8", errors="ignore")
            text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
            text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            return ToolResult(True, text[:5000], {"url": url})
        except Exception as exc:
            return ToolResult(False, f"Fetch failed: {exc}")
