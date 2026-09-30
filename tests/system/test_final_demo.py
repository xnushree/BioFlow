"""The scripted final demonstration runs end to end and tells a consistent story."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_final_demo_end_to_end(tmp_path: Path) -> None:
    report = tmp_path / "demo.md"
    result = subprocess.run(
        [sys.executable, "simulation/final_demo.py", "--report", str(report), "--checkpoints", "60",
         "--telemetry-out", str(tmp_path / "t.jsonl"), "--twin-out", str(tmp_path / "twin.png")],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )

    assert result.returncode == 0, result.stderr
    out = result.stdout
    for section in ("1. Load", "2. Run", "3. Faults", "4. Results", "5. Same workload"):
        assert section in out
    # Both injected faults were found from symptoms, correctly classified, and recovered from.
    assert "Detected:           2 (2 correctly classified" in out
    assert "False alarms: 0" in out
    assert "2 completed" in out and "0 unrecoverable" in out
    assert "Tasks completed:    408/408" in out
    # The comparison covers every policy plus the fault-free baseline, and the report is written.
    text = report.read_text(encoding="utf-8")
    for label in ("| fifo |", "| priority |", "| deadline |", "| cost |", "no faults"):
        assert label in text
    assert (tmp_path / "t.jsonl").stat().st_size > 0
    assert (tmp_path / "twin.png").read_bytes().startswith(b"\x89PNG")
