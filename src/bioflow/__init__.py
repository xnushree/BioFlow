"""BioFlow-X: fault-tolerant digital twin and orchestration for a simulated cell-culture lab."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bioflow-x")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
