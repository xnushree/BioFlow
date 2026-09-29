"""Environmental requirements for a culture.

These are abstract workflow constraints for the automation simulator (an
incubator must hold a plate within tolerance), not a biological model.
"""

from __future__ import annotations

from dataclasses import dataclass

from bioflow.domain._validation import require

DEFAULT_TEMPERATURE_C = 37.0
DEFAULT_CO2_PCT = 5.0
DEFAULT_TEMPERATURE_TOLERANCE_C = 0.5
DEFAULT_CO2_TOLERANCE_PCT = 0.5
MAX_PERCENT = 100.0


@dataclass(frozen=True)
class CultureConditions:
    """Target environment plus the allowed deviation before it counts as an excursion."""

    temperature_c: float = DEFAULT_TEMPERATURE_C
    co2_pct: float = DEFAULT_CO2_PCT
    temperature_tolerance_c: float = DEFAULT_TEMPERATURE_TOLERANCE_C
    co2_tolerance_pct: float = DEFAULT_CO2_TOLERANCE_PCT

    def __post_init__(self) -> None:
        require(
            0.0 <= self.co2_pct <= MAX_PERCENT,
            f"co2_pct must be within 0-{MAX_PERCENT:g}, got {self.co2_pct}",
        )
        require(
            self.temperature_tolerance_c > 0 and self.co2_tolerance_pct > 0,
            "Tolerances must be positive",
        )

    def is_within(self, temperature_c: float, co2_pct: float) -> bool:
        """True if an observed environment satisfies these conditions."""
        return (
            abs(temperature_c - self.temperature_c) <= self.temperature_tolerance_c
            and abs(co2_pct - self.co2_pct) <= self.co2_tolerance_pct
        )
