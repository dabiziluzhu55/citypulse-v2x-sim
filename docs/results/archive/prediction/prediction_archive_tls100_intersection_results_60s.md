# [Archived] TLS100 junction prediction: comparable 60-second results

All methods predict `vehicle_count` 60 seconds ahead on the same 100 traffic-light junction nodes and episode splits.

## Validation

| Method | MAE | RMSE | sMAPE | WMAPE |
| --- | ---: | ---: | ---: | ---: |
| persistence | 2.793 | 4.420 | 104.15% | 32.28% |
| moving_average | 2.528 | 4.089 | 94.75% | 29.22% |
| historical_average | 4.915 | 9.051 | 98.38% | 56.81% |
| XGBoost | 2.092 | 3.271 | 85.25% | 24.18% |
| STGCN | 1.847 | 2.924 | 82.27% | 21.34% |

## Test (ID)

| Method | MAE | RMSE | sMAPE | WMAPE |
| --- | ---: | ---: | ---: | ---: |
| persistence | 2.760 | 4.371 | 104.84% | 32.30% |
| moving_average | 2.506 | 4.070 | 95.41% | 29.32% |
| historical_average | 4.919 | 9.259 | 98.89% | 57.57% |
| XGBoost | 2.078 | 3.245 | 85.90% | 24.32% |
| STGCN | 1.862 | 2.977 | 83.06% | 21.79% |

## Test (OOD)

| Method | MAE | RMSE | sMAPE | WMAPE |
| --- | ---: | ---: | ---: | ---: |
| persistence | 2.775 | 4.436 | 107.08% | 32.55% |
| moving_average | 2.529 | 4.122 | 98.11% | 29.67% |
| historical_average | 5.698 | 11.095 | 104.20% | 66.82% |
| XGBoost | 2.195 | 3.464 | 89.03% | 25.74% |
| STGCN | 2.030 | 3.297 | 86.75% | 23.81% |

Note: sMAPE is zero-safe. MAPE excludes true vehicle counts below 0.5 after de-normalization and remains supplementary; use MAE, RMSE, sMAPE, and WMAPE for comparison.
