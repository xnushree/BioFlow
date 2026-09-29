"""Phase 0 smoke tests: confirm the toolchain works before any project code exists."""

import sys


def test_python_version_is_supported() -> None:
    assert sys.version_info >= (3, 11)


def test_running_inside_virtual_environment() -> None:
    # In a venv, sys.prefix points at .venv while sys.base_prefix points at the system Python.
    assert sys.prefix != sys.base_prefix, "Activate .venv before running tests"


def test_yaml_is_available() -> None:
    import yaml

    assert yaml.safe_load("capacity: 120") == {"capacity": 120}
