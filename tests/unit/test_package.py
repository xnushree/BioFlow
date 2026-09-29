"""Tests that the installed package is importable and versioned."""

import bioflow


def test_package_exposes_installed_version() -> None:
    assert bioflow.__version__ == "0.1.0"
