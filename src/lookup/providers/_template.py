"""Copy to <name>.py. Edit title, url and parseResult; other overrides are optional.

The leading underscore keeps this example out of provider discovery.
"""

from ..provider import JSONValue, ProviderBase, Result, jsonObject, jsonString


class Provider(ProviderBase):
    title = "Example"  # Popup label.
    method = "GET"  # GET or POST; never inferred from body().
    defaultConfig = {
        "timeout": 8,  # Positive seconds; inherited even if omitted here.
        # "token-path": "~/.config/example/token",  # Enable token-file auth.
        # Add provider-specific TOML options and their defaults here.
    }
    authScheme = "Bearer"  # Used with token-path, e.g. "Bot" for some APIs.
    missingOk = False  # True makes HTTP 404 pass None to parseResult().
    lowercase = False  # True lowercases the query before url() and body().

    @staticmethod
    def url(query: str) -> str:
        """Required: build the endpoint using the already URL-encoded query."""
        return f"https://example.org/search?q={query}"

    @staticmethod
    def body(query: str) -> JSONValue | None:
        """Optional for GET; required for POST. The query is plain text here."""
        return None  # GET must have no body.
        # For method = "POST", replace with: return {"query": query}
        # Empty objects/arrays are valid; None does not represent JSON null.

    @staticmethod
    def parseResult(data: JSONValue | None) -> Result | None:
        """Required: convert the API's JSON to a result, or None for no match."""
        if data is None:
            return None
        return Result(text=jsonString(jsonObject(data).get("answer")))
        # Optional Result fields: title, detail, links=(Link(...),), markdown=True.
        # Raise SourceError("safe message") for a provider-specific error.

    # Advanced overrides: normally leave these inherited. They are shown here
    # only for providers that need extra validation or a different fetch path.
    #
    # def __init__(self, options: dict[str, object] | None = None):
    #     super().__init__(options)
    #     # Validate provider-specific settings in self.config here.
    #
    # @property
    # def timeout(self) -> float:
    #     return super().timeout  # Prefer defaultConfig["timeout"] instead.
    #
    # def fetchJson(self, query: str) -> JSONValue | None:
    #     return super().fetchJson(query)  # Custom transport/authentication.
    #     # Keep this returning JSON: --test-source calls it directly.
    #
    # def lookup(self, query: str) -> Result | None:
    #     return super().lookup(query)  # Normally fetchJson + parseResult.
    #     # Overriding this alone does not affect --test-source.
