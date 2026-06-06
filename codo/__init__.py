"""Codo desktop package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("codo")
except PackageNotFoundError:
    __version__ = "0+unknown"
