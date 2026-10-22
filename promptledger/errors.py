"""User-facing errors whose messages are safe to write into reports."""


class ConfigError(ValueError):
    """An invalid suite, replay fixture, baseline, or command option."""


class ProviderError(RuntimeError):
    """A provider failure, without credentials or raw HTTP bodies."""
