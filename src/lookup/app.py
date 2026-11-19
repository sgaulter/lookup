"""Configuration, command-line interface and PRIMARY selection input."""

from __future__ import annotations

import argparse
import copy
import json
import os
import signal
import subprocess
import sys
import tomllib
from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any

from .popup import Lookup, showPopup
from .provider import ConfigError, SourceError
from .providers import availableProviders, loadProvider

# Arrays are argv, never shell commands. No provider identity affects popup behaviour.
defaultConfig: dict[str, Any] = {
    "providers": ["dictionary", "wikipedia"],
    "selection-max-length": 100,
    "primary-paste": "auto",
    "web-search-url": "https://duckduckgo.com/?q={query}",
    "popup": {
        "window-name": "lookup",
        "window-class": "lookup",
        "width": 520,
        "height": "auto",
        "position": "default",
        "close-on-unfocus": False,
        "additional-yad-flags": [],
    },
    "provider": {},
}


def primaryPasteCommand(setting: str | list[str]) -> list[str]:
    """Resolve auto using the package default and current display session."""
    if isinstance(setting, list):
        return setting
    backend = os.environ.get("LOOKUP_CLIPBOARD_BACKEND", "auto")
    if backend == "auto":
        # Prefer Wayland when DISPLAY also exists for XWayland.
        backend = (
            "x11"
            if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
            else "wayland"
        )
    if backend == "wayland":
        return ["wl-paste", "--primary", "--no-newline"]
    if backend == "x11":
        return ["xclip", "-selection", "primary", "-out"]
    raise ConfigError("LOOKUP_CLIPBOARD_BACKEND must be auto, wayland or x11")


def checkKeys(options: dict, defaults: dict, context: str) -> None:
    unknown = options.keys() - defaults.keys()
    if unknown:
        raise ConfigError(f"{context}: unknown options: {', '.join(sorted(unknown))}")


def configPath() -> Path:
    directory = os.environ.get("XDG_CONFIG_HOME")
    return (
        Path(directory) if directory else Path.home() / ".config"
    ) / "lookup/config.toml"


def loadConfig(path: Path | None = None) -> dict[str, Any]:
    """Read TOML and merge defaults; an absent default file is optional."""
    config = copy.deepcopy(defaultConfig)
    try:
        with (path or configPath()).open("rb") as stream:
            options = tomllib.load(stream)
    except FileNotFoundError:
        if path is not None:
            raise ConfigError(f"Configuration file not found: {path}") from None
        return config
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Invalid TOML: {error}") from error
    checkKeys(options, config, "config")
    popup = options.get("popup", {})
    if not isinstance(popup, dict):
        raise ConfigError("popup must be a table")
    checkKeys(popup, config["popup"], "popup")
    return config | options | {"popup": config["popup"] | popup}


def validateConfig(config: dict[str, Any]) -> None:
    """Validate configuration before executing commands."""
    for key in ("providers", "primary-paste"):
        value = config[key]
        if key == "primary-paste" and value == "auto":
            continue
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise ConfigError(f"{key} must be a nonempty array of nonempty strings")
    if isinstance(config["primary-paste"], list) and any(
        "\0" in item for item in config["primary-paste"]
    ):
        raise ConfigError("primary-paste arguments cannot contain NUL")
    if (
        type(config["selection-max-length"]) is not int
        or config["selection-max-length"] <= 0
    ):
        raise ConfigError("selection-max-length must be a positive integer")
    if not isinstance(config["web-search-url"], str):
        raise ConfigError("web-search-url must be a string (empty disables the link)")
    popup = config["popup"]
    for key in ("window-name", "window-class"):
        if not isinstance(popup[key], str) or "\0" in popup[key]:
            raise ConfigError(f"popup.{key} must be a string without NUL")
    for key in ("width", "height"):
        value = popup[key]
        if key == "height" and value == "auto":
            continue
        if type(value) is not int or value <= 0:
            raise ConfigError(
                f"popup.{key} must be a positive integer"
                + (' or "auto"' if key == "height" else "")
            )
    if popup["position"] not in ("default", "pointer"):
        raise ConfigError('popup.position must be "default" or "pointer"')
    if type(popup["close-on-unfocus"]) is not bool:
        raise ConfigError("popup.close-on-unfocus must be a boolean")
    flags = popup["additional-yad-flags"]
    if not isinstance(flags, list) or not all(
        isinstance(item, str) and "\0" not in item for item in flags
    ):
        raise ConfigError(
            "popup.additional-yad-flags must be an array of strings without NUL"
        )
    providers = config["provider"]
    if not isinstance(providers, dict) or not all(
        isinstance(value, dict) for value in providers.values()
    ):
        raise ConfigError("provider entries must be tables")
    available = set(availableProviders())
    unknown = (set(config["providers"]) | providers.keys()) - available
    if unknown:
        raise ConfigError(f"Unknown providers: {', '.join(sorted(unknown))}")


def normaliseSelection(text: str, maxLength: int = 100) -> str:
    return " ".join(text.split())[:maxLength]


def readSelection(command: list[str], maxLength: int) -> str:
    """Read PRIMARY using the configured command, without a shell."""
    try:
        output = subprocess.run(
            command, capture_output=True, text=True, timeout=3, check=True
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise SourceError(
            "Cannot read PRIMARY selection; check primary-paste and the display session"
        ) from error
    query = normaliseSelection(output.stdout, maxLength)
    if not query:
        raise SourceError("Nothing selected")
    return query


def makeParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Look up selected text in a Linux desktop popup."
    )
    parser.add_argument("--config", type=Path, help="TOML configuration path")
    parser.add_argument("--list-providers", dest="listProviders", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--default-config",
        dest="printDefaultConfig",
        action="store_true",
        help="print the commented default TOML configuration",
    )
    mode.add_argument(
        "--providers",
        nargs="+",
        choices=availableProviders(),
        help="override the configured providers for this invocation",
    )
    mode.add_argument(
        "--test-source",
        dest="testSource",
        choices=availableProviders(),
        help="print raw decoded JSON without parsing or opening a popup",
    )
    return parser


def interrupted(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    args = makeParser().parse_args()
    if args.printDefaultConfig:
        sys.stdout.write(
            files("lookup").joinpath("default-config.toml").read_text(encoding="utf-8")
        )
        return 0
    if args.listProviders:
        print("\n".join(availableProviders()))
        return 0
    previousHandler = signal.signal(signal.SIGTERM, interrupted)
    try:
        config = loadConfig(args.config)
        if args.providers is not None:
            config["providers"] = args.providers
        if args.testSource is not None:
            config["providers"] = [args.testSource]
        validateConfig(config)
        sources = {
            name: loadProvider(name, config["provider"].get(name, {}))
            for name in config["providers"]
        }
        query = readSelection(
            primaryPasteCommand(config["primary-paste"]), config["selection-max-length"]
        )
        if args.testSource is not None:
            print(
                json.dumps(
                    sources[args.testSource].fetchJson(query),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            popup = config["popup"]
            showPopup(
                Lookup(query, sources, config["web-search-url"]),
                name=popup["window-name"],
                windowClass=popup["window-class"],
                width=popup["width"],
                height=None if popup["height"] == "auto" else popup["height"],
                mouse=popup["position"] == "pointer",
                closeOnUnfocus=popup["close-on-unfocus"],
                additionalYadFlags=popup["additional-yad-flags"],
            )
        return 0
    except KeyboardInterrupt:
        return 130
    except (ConfigError, SourceError, OSError) as error:
        print(f"lookup: {error}", file=sys.stderr)
        if args.testSource is None:
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["notify-send", "Lookup", str(error)], timeout=3, check=False
                )
        return 1
    finally:
        signal.signal(signal.SIGTERM, previousHandler)
