"""FDPneo Index — a directory of FAIR Data Points (ADR-0001)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fdpneo-index")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"
