"""Tune the cost scheduler's weights by random search, with a train/test split.

    objective(seed) = (fraction of experiments late) + (makespan / FIFO makespan - 1)

The makespan term is relative to FIFO on the same generated workload, so small
and large scenarios weigh equally. FIFO itself scores exactly its own fraction
of late experiments. Lower is better.

Random search in log-space (weights span orders of magnitude) is used because
the objective comes from a noisy, discrete simulation: there is no gradient,
and it is cheap to explain. The current defaults are always evaluated as one
candidate, so tuning can only recommend a change if something beats them.

Weights are chosen on *training* seeds and judged on *held-out* seeds they
never saw; improvement on training seeds alone would just be memorising those
particular workloads.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, replace
from statistics import mean
from typing import Any

import yaml

from bioflow.analytics.performance import run_one
from bioflow.scheduling.config import CostSettings, CostWeights, SchedulingConfig
from bioflow.workload import WorkloadSpec

# Search range per weight (log-uniform). Zero is excluded on purpose: a zero weight switches a term off,
# which the search can approach with the lower bound.
WEIGHT_BOUNDS: dict[str, tuple[float, float]] = {
    "travel": (0.05, 10.0),
    "switching": (0.1, 50.0),
    "delay": (0.001, 1.0),
    "idle": (0.0005, 0.5),
    "deadline": (1.0, 500.0),
}


@dataclass(frozen=True)
class Evaluation:
    weights: CostWeights
    objective: float  # mean over seeds
    per_seed: tuple[float, ...]


@dataclass(frozen=True)
class TuningResult:
    default: Evaluation  # current weights, training seeds
    best: Evaluation  # best candidate, training seeds
    candidates: tuple[Evaluation, ...]
    test_default: Evaluation  # held-out seeds
    test_best: Evaluation
    test_fifo_objective: float  # FIFO's own score on the held-out seeds (its late fraction)

    @property
    def generalises(self) -> bool:
        """True if the tuned weights also beat the defaults on seeds they were not tuned on."""
        return self.test_best.objective < self.test_default.objective


def objective(row: dict[str, Any], fifo_row: dict[str, Any]) -> float:
    return row["deadline_miss_rate"] + (row["makespan_min"] / fifo_row["makespan_min"] - 1.0)


def sample_weights(rng: random.Random) -> CostWeights:
    values = {name: math.exp(rng.uniform(math.log(low), math.log(high))) for name, (low, high) in WEIGHT_BOUNDS.items()}
    return CostWeights(**{name: round(value, 4) for name, value in values.items()})


def evaluate(weights: CostWeights, spec: WorkloadSpec, seeds: Sequence[int],
             fifo_rows: dict[int, dict[str, Any]], base: CostSettings) -> Evaluation:
    config = SchedulingConfig(cost=replace(base, weights=weights))
    scores = tuple(objective(run_one(spec, "cost", seed, config), fifo_rows[seed]) for seed in seeds)
    return Evaluation(weights, mean(scores), scores)


def tune(
    spec: WorkloadSpec,
    train_seeds: Sequence[int],
    test_seeds: Sequence[int],
    candidates: int,
    search_seed: int = 0,
    base: CostSettings | None = None,
    progress: Callable[[int, Evaluation], None] | None = None,
) -> TuningResult:
    base = base or CostSettings()
    fifo = {seed: run_one(spec, "fifo", seed) for seed in (*train_seeds, *test_seeds)}
    rng = random.Random(search_seed)

    default = evaluate(base.weights, spec, train_seeds, fifo, base)
    evaluations = [default]
    if progress:
        progress(0, default)
    for index in range(1, candidates + 1):
        evaluation = evaluate(sample_weights(rng), spec, train_seeds, fifo, base)
        evaluations.append(evaluation)
        if progress:
            progress(index, evaluation)
    best = min(evaluations, key=lambda e: e.objective)

    return TuningResult(
        default=default, best=best, candidates=tuple(evaluations),
        test_default=evaluate(base.weights, spec, test_seeds, fifo, base),
        test_best=evaluate(best.weights, spec, test_seeds, fifo, base),
        test_fifo_objective=mean(objective(fifo[seed], fifo[seed]) for seed in test_seeds),
    )


def weights_yaml(weights: CostWeights, base: CostSettings, note: str) -> str:
    """A complete scheduling config with the given weights, ready to use as ``scheduling_config``."""
    data = {"cost": {"weights": {f.name: getattr(weights, f.name) for f in fields(CostWeights)},
                     "default_step_min": base.default_step_min, "max_critical_ratio": base.max_critical_ratio}}
    return f"# {note}\n" + yaml.safe_dump(data, sort_keys=False)
