"""Kagi lookup provider."""

from ..provider import (
    JSONValue,
    Link,
    ProviderBase,
    Result,
    SourceError,
    jsonObject,
    jsonString,
)


class KagiProvider(ProviderBase):
    title = "Kagi"
    method = "POST"
    authScheme = "Bot"
    defaultConfig = {"timeout": 25, "token-path": "~/.config/kagi/token"}

    @staticmethod
    def url(query: str) -> str:
        return "https://kagi.com/api/v0/fastgpt"

    @staticmethod
    def body(query: str) -> dict[str, JSONValue | None]:
        """Build the JSON prompt from the plain query."""
        return {"query": f"Briefly explain: {query}"}

    @staticmethod
    def parseResult(data: JSONValue | None) -> Result | None:
        """Extract the Markdown answer and up to five references."""
        if data is None:
            return None
        entry = jsonObject(data)
        answerData = entry.get("data")
        answer = jsonObject(answerData) if answerData is not None else {}
        output = answer.get("output")
        if output is not None and output != "":
            references = answer.get("references")
            links = []
            for i, item in enumerate(
                references[:5] if isinstance(references, list) else [], 1
            ):
                if not isinstance(item, dict):
                    continue
                title, url = item.get("title"), item.get("url")
                if isinstance(title, str) and isinstance(url, str) and url:
                    links.append(Link(f"[{i}] {title}", url))
            return Result(text=jsonString(output), links=tuple(links), markdown=True)
        if entry.get("error"):
            raise SourceError("Kagi returned an API error")
        return None


Provider = KagiProvider
