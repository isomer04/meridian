"""Package version exposed by the API."""

from importlib.metadata import PackageNotFoundError, version

try:
    VERSION = version("meridian")
except PackageNotFoundError:
    VERSION = "0.0.0+unknown"
