# Anomaly detection on simulated equipment telemetry (optional layer)

The rule-based fault detector (Phase 16) is the primary, explainable mechanism. Its rules have
deliberate thresholds (a slow drive is diagnosed above 2x nominal; a temperature or CO2 excursion
beyond +-0.5), which keep false alarms at zero but make **gradual degradation below those thresholds
invisible**. This optional layer asks whether a model trained only on healthy operation can see what the
rules cannot.

Reproduce: `python simulation/anomaly_detection.py` (about a minute; report in
`results/reports/anomaly_report.md`).

## Method

1. **Features.** `TelemetryFeatureCollector`, a non-critical bus observer, summarises each machine per
   60-minute window from observable telemetry only:
   - robots: steps, drive-time ratio per step (mean, minimum), waits, failed picks, transports;
   - incubators: temperature and CO2 deviation from setpoint (mean, maximum, spread);
   - stations: runs started and finished.
2. **Model.** One Isolation Forest per equipment kind (scikit-learn), trained on **healthy runs only**
   (workload B, 6 seeds). The alarm threshold is the 99th percentile of healthy scores, which gives a
   target false-alarm rate of about 1 %.
3. **Test.** New seeds with one of each of five faults, three of them *subtle* (below the rule
   thresholds by design), labelled per window from ground truth, compared fault by fault with the
   rule-based detector.

## A limitation found, and fixed

The first version could not see even an obvious 4x robot slowdown (1 of 6 caught). The cause is how
Isolation Forests work: they isolate points by random splits **within the training range** of each
feature. In the simulation a healthy robot's drive-time ratio is *exactly* 1.0 every time, so that
feature has no range and the forest never splits on it. The dimension that matters most is the one it
is blind to. Features that never varied in healthy data now get an **extrapolation guard**: a clear
departure from their healthy constant is anomalous in itself.

## Results

| Fault (6 runs each) | Rules | Isolation Forest only | Forest + guard |
|---|---|---|---|
| slow drive 1.5x (subtle) | 0/6 | 0/6 | 6/6 |
| temperature +0.4 degC (subtle) | 0/6 | 6/6 | 6/6 |
| CO2 +0.4 % (subtle) | 0/6 | 5/6 | 5/6 |
| slow drive 4x | 6/6 | 1/6 | 6/6 |
| temperature +2 degC | 6/6 | 6/6 | 6/6 |

False alarms on held-out healthy runs: 0.4 % of windows (target 1 %). Window-level ROC AUC:
incubators 0.98, robots 0.65. Robot recall is low largely because a degraded robot shows nothing while
it is parked, and those idle windows still count as "faulty" in the ground truth.

**Conclusion:** the model complements the rules. It caught 17 of the 18 subtle degradations that the rules
cannot see by design, without raising more false alarms. It does not replace them: the rules remain
the explainable, zero-false-alarm path for clear faults and drive recovery.

## Caveats

- The simulated drive has no noise, so any slowdown is visible once guarded. A real drive varies, and
  small slowdowns would be much harder to separate from noise.
- Windows are labelled by ground truth, including windows where a fault had no observable effect.
- This is anomaly detection on *simulated* telemetry. It makes no claim about predicting failures of
  real equipment.
