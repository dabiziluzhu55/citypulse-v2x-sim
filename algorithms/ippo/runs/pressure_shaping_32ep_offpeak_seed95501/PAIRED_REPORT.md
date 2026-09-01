# IPPO pressure shaping 32ep paired evaluation

- Scenario: xiongan_20 (20 TLS), period: off_peak, duration: 300 s
- Training: 32 episodes, seeds 95501-95532, 8 workers, sync batches
- Baseline: standard V5A reward; Shaped: V5A + density-gated MaxPressure regret
- Evaluation: held-out seeds 66501-66508 (paired), 8 workers
- Delta = shaped - baseline; CI = bootstrap 95% (10k resamples, seed 20260811)

| metric | baseline mean | shaped mean | mean delta | median delta | win/loss/tie | 95% CI |
|---|---|---|---|---|---|---|
| controlled_avg_waiting_time_s | 8.839 | 8.54 | -0.2988 | -0.325 | 8/0/0 | [-0.3613, -0.2262] |
| avg_travel_time_s | 139.9 | 139.8 | -0.09375 | -0.08 | 8/0/0 | [-0.1288, -0.06375] |
| avg_waiting_time_s | 19.16 | 19.05 | -0.1175 | -0.13 | 6/2/0 | [-0.1875, -0.0475] |
| avg_queue_length_veh | 0.1187 | 0.1187 | +0 | +0 | 0/0/8 | [+0, +0] |
| throughput_veh_per_h | 1845 | 1888 | +43.5 | +48 | 8/0/0 | [+36, +51] |
| fuel_intensity_L_per_100km | 14.96 | 14.94 | -0.025 | -0.02 | 7/0/1 | [-0.03625, -0.01375] |
| avg_decision_latency_ms | 19.38 | 19.72 | +0.3381 | +0.3315 | 2/6/0 | [-0.4165, +1.011] |
| severe_conflict_exposure_per_10000 | N/A | N/A | N/A | N/A | N/A | N/A |
| emergency_braking_exposure_per_1000 | 1791 | 1800 | +8.934 | +2.445 | 4/4/0 | [-9.464, +27.94] |
| arrived | 153.8 | 157.4 | +3.625 | +4 | 8/0/0 | [+2.875, +4.25] |
| all_waiting_total_s | 1.801e+04 | 1.79e+04 | -108.8 | -119.7 | 6/2/0 | [-174.6, -42.8] |
| end_waiting_total_s | 1.801e+04 | 1.79e+04 | -108.8 | -119.7 | 6/2/0 | [-175.2, -42.04] |
| hard_braking | 3654 | 3652 | -2.25 | -5 | 4/4/0 | [-30.88, +28.88] |
| completion_rate | 0.1636 | 0.1675 | +0.003858 | +0.004255 | 8/0/0 | [+0.003191, +0.004522] |

## Per-seed controlled avg waiting (s)

| seed | baseline | shaped | delta |
|---|---|---|---|
| 66501 | 8.910 | 8.490 | -0.420 |
| 66502 | 8.770 | 8.660 | -0.110 |
| 66503 | 8.960 | 8.680 | -0.280 |
| 66504 | 8.870 | 8.680 | -0.190 |
| 66505 | 8.790 | 8.430 | -0.360 |
| 66506 | 9.030 | 8.690 | -0.340 |
| 66507 | 8.480 | 8.170 | -0.310 |
| 66508 | 8.900 | 8.520 | -0.380 |
