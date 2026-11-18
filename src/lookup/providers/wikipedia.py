"""Wikipedia lookup provider."""

from ..provider import JSONValue, Link, ProviderBase, Result, jsonObject, jsonString


class WikipediaProvider(ProviderBase):
    title = "Wikipedia"
    missingOk = True

    @staticmethod
    def url(query: str) -> str:
        return (
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{query}?redirect=true"
        )

    @staticmethod
    def parseResult(data: JSONValue | None) -> Result | None:
        """Extract a summary and its article link, if available."""
        if data is None:
            return None
        entry = jsonObject(data)
        extract = entry.get("extract")
        if extract is None or extract == "":
            return None
        contentUrls = entry.get("content_urls")
        desktop = contentUrls.get("desktop") if isinstance(contentUrls, dict) else None
        url = desktop.get("page") if isinstance(desktop, dict) else None
        description = entry.get("description")
        return Result(
            title=jsonString(entry.get("title")),
            detail=description if isinstance(description, str) else "",
            text=jsonString(extract),
            links=(Link("Read on Wikipedia", url),)
            if isinstance(url, str) and url
            else (),
        )


Provider = WikipediaProvider
