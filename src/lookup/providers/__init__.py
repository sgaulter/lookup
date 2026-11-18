"""Discover provider modules without importing unused providers."""

from importlib import import_module
from inspect import isabstract
from pkgutil import iter_modules

from ..provider import ConfigError, ProviderBase


def availableProviders() -> list[str]:
    return sorted(
        info.name
        for info in iter_modules(__path__)
        if not info.name.startswith("_") and not info.ispkg
    )


def loadProvider(name: str, options: dict[str, object]) -> ProviderBase:
    if name not in availableProviders():
        raise ConfigError(f"Unknown provider: {name}")
    try:
        module = import_module(f"{__name__}.{name}")
        provider = getattr(module, "Provider", None)
        if not isinstance(provider, type) or not issubclass(provider, ProviderBase):
            raise ConfigError(
                f"Provider {name} must export a ProviderBase subclass as Provider"
            )
        if isabstract(provider):
            missing = ", ".join(sorted(provider.__abstractmethods__))
            raise ConfigError(f"Provider {name} must implement: {missing}")
        return provider(options)
    except ConfigError:
        raise
    except Exception as error:  # noqa: BLE001 - report broken selected plugins cleanly
        raise ConfigError(
            f"Cannot load provider {name} ({type(error).__name__})"
        ) from error
