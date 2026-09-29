"""Entry point for ``python -m bioflow``. Prints version info until the scenario runner exists."""

import logging

from bioflow import __version__
from bioflow.telemetry.logger import configure_logging


def main() -> None:
    configure_logging("INFO")
    logging.getLogger("bioflow").info("BioFlow-X %s installed and importable", __version__)


if __name__ == "__main__":
    main()
