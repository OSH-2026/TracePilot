# feed_scroll baseline vs intervention experiment

- package: `com.android.chrome`
- duration per run: 20s
- repetitions: 3 baseline + 3 intervention
- actuator: Chrome render-thread renice/top-app cpuset guard, target nice -10

Primary outputs:

- `experiment_manifest.json`: per-run metadata, metrics, actuator audit summary
- `experiment_summary.json`: aggregate baseline/intervention comparison
- `experiment_summary.csv`: compact table for reports

Raw `.bin`, `.perfetto-trace`, and generated CSV files should follow the repository raw-artifact policy.
