"""Tests for cost-weight tuning (tiny workloads so they stay fast)."""

import math
import random
from dataclasses import fields, replace
from pathlib import Path

import pytest
import yaml

from bioflow.analytics.performance import load_benchmark_plan
from bioflow.analytics.tuning import WEIGHT_BOUNDS, objective, sample_weights, tune, weights_yaml
from bioflow.scheduling.config import CostSettings, CostWeights, parse_scheduling_config

ROOT = Path(__file__).parents[3]


@pytest.fixture(autouse=True)
def run_from_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)


def test_objective_is_relative_to_fifo() -> None:
    fifo = {"deadline_miss_rate": 0.5, "makespan_min": 1000.0}

    assert objective(fifo, fifo) == pytest.approx(0.5)  # same makespan as FIFO: only lateness counts
    assert objective({"deadline_miss_rate": 0.2, "makespan_min": 900.0}, fifo) == pytest.approx(0.2 - 0.1)


def test_sampled_weights_stay_in_bounds_and_are_reproducible() -> None:
    samples = [sample_weights(random.Random(7)) for _ in range(2)]
    assert samples[0] == samples[1]

    rng = random.Random(1)
    for _ in range(200):
        weights = sample_weights(rng)
        for f in fields(CostWeights):
            low, high = WEIGHT_BOUNDS[f.name]
            assert low * 0.999 <= getattr(weights, f.name) <= high * 1.001


def test_search_is_log_uniform() -> None:
    """Half of the samples should fall below the geometric midpoint of each range."""
    rng = random.Random(3)
    below = sum(sample_weights(rng).deadline < math.sqrt(1.0 * 500.0) for _ in range(400))

    assert 160 < below < 240


def test_tuning_keeps_defaults_as_a_candidate_and_validates_on_held_out_seeds() -> None:
    tiny = replace(load_benchmark_plan(Path("configs/benchmarks.yaml")).workloads["A"], plates=4)

    result = tune(tiny, train_seeds=[1], test_seeds=[2], candidates=2)

    assert len(result.candidates) == 3
    assert result.candidates[0].weights == CostSettings().weights
    assert result.best.objective == min(e.objective for e in result.candidates)
    assert len(result.test_best.per_seed) == 1
    assert result.generalises == (result.test_best.objective < result.test_default.objective)
    assert result.test_fifo_objective >= 0.0  # FIFO's own late fraction, not a constant


def test_tuned_weights_file_is_a_valid_scheduling_config() -> None:
    weights = CostWeights(travel=2.0, switching=1.0, delay=0.1, idle=0.01, deadline=80.0)

    text = weights_yaml(weights, CostSettings(), "note")

    assert text.startswith("# note")
    assert parse_scheduling_config(yaml.safe_load(text)).cost.weights == weights
