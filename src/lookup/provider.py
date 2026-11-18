"""Shared provider configuration, HTTP requests and result types."""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

JSONValue: TypeAlias = (
    dict[str, "JSONValue | None"] | list["JSONValue | None"] | str | int | float | bool
)

userAgent = "lookup/0.1.0"

maxResponseBytes = 2 * 1024 * 1024


class SourceError(Exception):
    """A message safe to display in the source's result panel."""


def jsonObject(value: JSONValue | None) -> dict[str, JSONValue | None]:
    """Validate and narrow a decoded JSON object."""
    if not isinstance(value, dict):
        raise SourceError("Unexpected API response: expected an object")
    return value


def jsonArray(value: JSONValue | None) -> list[JSONValue | None]:
    """Validate and narrow a decoded JSON array."""
    if not isinstance(value, list):
        raise SourceError("Unexpected API response: expected an array")
    return value


def jsonString(value: JSONValue | None) -> str:
    """Validate and narrow a decoded JSON string."""
    if not isinstance(value, str):
        raise SourceError("Unexpected API response: expected a string")
    return value


@dataclass(frozen=True)
class Link:
    """A source reference with a display label and destination URL."""

    title: str
    url: str


@dataclass(frozen=True)
class Result:
    """Display content returned by a source parser, without raw HTML."""

    text: str
    title: str = ""
    detail: str = ""
    links: tuple[Link, ...] = ()
    markdown: bool = False


class ConfigError(ValueError):
    """Invalid application or provider configuration."""


def mergeConfig(
    defaults: dict[str, object], options: dict[str, object], context: str
) -> dict[str, object]:
    """Merge known options and check their types against their defaults."""
    unknown = options.keys() - defaults.keys()
    if unknown:
        raise ConfigError(f"{context}: unknown options: {', '.join(sorted(unknown))}")
    merged = defaults | options
    for key, value in merged.items():
        default = defaults[key]
        valid = type(value) is type(default)
        if type(default) in (int, float):
            valid = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
        if not valid:
            raise ConfigError(f"{context}.{key}: expected {type(default).__name__}")
    return merged


def requestJson(
    url: str,
    *,
    method: str = "GET",
    body: JSONValue | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8,
    missingOk: bool = False,
) -> JSONValue | None:
    """Send an explicit GET or JSON POST, rejecting inconsistent bodies."""
    if method not in ("GET", "POST"):
        raise SourceError('Request method must be "GET" or "POST"')
    if method == "GET" and body is not None:
        raise SourceError("GET requests must not have a body")
    if method == "POST" and body is None:
        raise SourceError(
            "POST requests require a JSON body; use {} for an empty object"
        )
    requestHeaders = {"User-Agent": userAgent, "Accept": "application/json"}
    data = None
    if body is not None:
        try:
            data = json.dumps(body, allow_nan=False).encode()
        except (TypeError, ValueError) as error:
            raise SourceError(
                "Request body must be JSON-serializable with finite numbers"
            ) from error
        requestHeaders["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=requestHeaders, method=method)
    for name, value in (headers or {}).items():
        if name.lower() == "authorization":
            # urllib otherwise forwards this credential when following redirects.
            request.add_unredirected_header(name, value)
        else:
            request.add_header(name, value)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(maxResponseBytes + 1)
    except HTTPError as error:
        if missingOk and error.code == 404:
            return None
        raise SourceError(f"HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        # Do not expose headers, tokens, or response bodies in diagnostics.
        raise SourceError(
            "Could not reach the API (network error or timeout)"
        ) from error
    if len(payload) > maxResponseBytes:
        raise SourceError("API response exceeded 2 MiB")
    try:
        return cast(JSONValue | None, json.loads(payload))
    except (ValueError, UnicodeError) as error:
        raise SourceError("API returned invalid JSON") from error


class ProviderBase(ABC):
    """Provider contract: implement url() and parseResult(), and set title.

    Optional: body() for POST, defaultConfig for settings, and the behaviour
    flags below. The inherited constructor, fetchJson() and lookup() handle
    configuration, authentication and fetching; providers need not override them.
    """

    defaultConfig: dict[str, object] = {"timeout": 8}
    title = "Lookup"
    method = "GET"
    authScheme = "Bearer"
    missingOk = False
    lowercase = False

    @staticmethod
    @abstractmethod
    def url(query: str) -> str:
        """Required: build a URL from an already URL-encoded query."""
        raise NotImplementedError

    @staticmethod
    def body(query: str) -> JSONValue | None:
        """Optional for GET; required for POST: return JSON from the plain query."""
        return None

    @staticmethod
    @abstractmethod
    def parseResult(data: JSONValue | None) -> Result | None:
        """Required: convert decoded JSON to display content, or None for no match."""
        raise NotImplementedError

    # Shared implementation: ordinary providers inherit everything below.

    def __init__(self, options: dict[str, object] | None = None):
        if self.method not in ("GET", "POST"):
            raise ConfigError(f'{self.title}.method must be "GET" or "POST"')
        defaults = ProviderBase.defaultConfig | self.defaultConfig
        self.config = mergeConfig(defaults, options or {}, self.title)
        if self.timeout <= 0:
            raise ConfigError(f"{self.title}.timeout must be positive")
        tokenPath = self.config.get("token-path")
        if tokenPath is not None and (
            not isinstance(tokenPath, str) or not tokenPath.strip()
        ):
            raise ConfigError(f"{self.title}.token-path must be a nonempty string")

    @property
    def timeout(self) -> float:
        return float(cast(float, self.config["timeout"]))

    def lookup(self, query: str) -> Result | None:
        return self.parseResult(self.fetchJson(query))

    def fetchJson(self, query: str) -> JSONValue | None:
        """Fetch JSON with the provider's configuration, without parsing it."""
        if self.lowercase:
            query = query.lower()
        headers = {}
        tokenPath = self.config.get("token-path")
        if isinstance(tokenPath, str):
            try:
                token = Path(tokenPath).expanduser().read_text().strip()
            except (OSError, UnicodeError) as error:
                raise SourceError(f"Cannot read {self.title} token file") from error
            if not token or "\n" in token or "\r" in token:
                raise SourceError(f"{self.title} token is empty or invalid")
            headers["Authorization"] = f"{self.authScheme} {token}"
        return requestJson(
            self.url(quote(query, safe="")),
            method=self.method,
            body=self.body(query),
            headers=headers,
            timeout=self.timeout,
            missingOk=self.missingOk,
        )
