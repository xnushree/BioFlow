# Anomaly detection on simulated equipment telemetry

Isolation Forest per equipment kind, trained on 2838 healthy 60-minute windows (workload B, seeds [1, 2, 3, 4, 5, 6]); alarm threshold at the 99th percentile of healthy scores. 101 s.

**False alarms on held-out healthy runs** (seeds [21, 22]): 0.4% of 968 windows with the guard, 0.4% forest only (target 1%).

Features that never varied in healthy data (the Isolation Forest cannot use them, so they get an extrapolation guard): {'ROBOT': ['mean_step_ratio', 'min_step_ratio', 'pick_failures'], 'INCUBATOR': ['readings'], 'MEDIA_STATION': [], 'IMAGING_STATION': []}.

## Which faults were caught

Test runs: seeds [11, 12, 13, 14, 15, 16], one of each fault per run. *Subtle* faults are deliberately below the rule-based thresholds (slow drive alarms at 2x, excursions at +-0.5).

| Fault | Runs | Caught by rules | Isolation Forest only | Forest + degenerate-feature guard |
|---|---|---|---|---|
| slow drive 1.5x (subtle) | 6 | 0/6 | 0/6 | 6/6 |
| temperature +0.4 degC (subtle) | 6 | 0/6 | 6/6 | 6/6 |
| CO2 +0.4 % (subtle) | 6 | 0/6 | 5/6 | 5/6 |
| slow drive 4x | 6 | 6/6 | 1/6 | 6/6 |
| temperature +2 degC | 6 | 6/6 | 6/6 | 6/6 |

## Window-level scores (faulted runs)

| Kind | Windows | Anomalous | Flagged | Precision | Recall | ROC AUC |
|---|---|---|---|---|---|---|
| ROBOT | 780 | 61 | 39 | 0.82 | 0.52 | 0.65 |
| INCUBATOR | 1040 | 90 | 75 | 0.89 | 0.74 | 0.98 |
| MEDIA_STATION | 520 | 0 | 0 | - | - | - |
| IMAGING_STATION | 520 | 0 | 1 | 0.00 | - | - |

## Caveats

- The simulated drive has no noise, so a healthy robot's drive-time ratio is exactly 1.0 and any slowdown is trivially visible once guarded. A real drive varies, and small slowdowns would be harder to separate from noise.
- Faults are labelled per window by ground truth; a fault that begins late in a window, or is not exercised (an idle robot), contributes windows the model could not possibly flag.
- This is anomaly detection on *simulated* telemetry. It says nothing about predicting failures of real equipment.
