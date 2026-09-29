"""Shared fixtures for domain tests."""

import pytest

from bioflow.domain import Operation, Protocol, ProtocolStep


@pytest.fixture
def basic_protocol() -> Protocol:
    """INCUBATE -> MEDIA_EXCHANGE -> INCUBATE -> IMAGE -> ARCHIVE."""
    return Protocol(
        name="basic",
        cell_type="HEK293",
        steps=(
            ProtocolStep(Operation.INCUBATE, 720),
            ProtocolStep(Operation.MEDIA_EXCHANGE),
            ProtocolStep(Operation.INCUBATE, 1440),
            ProtocolStep(Operation.IMAGE),
            ProtocolStep(Operation.ARCHIVE),
        ),
    )
