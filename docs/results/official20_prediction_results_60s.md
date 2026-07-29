# Official-20 intersection prediction: comparable 60-second results

All methods predict `vehicle_count` 60 seconds ahead on the same 20 intersections and episode splits.

## Validation

| Method | MAE | RMSE | WMAPE |
| --- | ---: | ---: | ---: |
| persistence | 4.545 | 6.044 | 21.33% |
| moving_average | 4.410 | 5.955 | 20.69% |
| historical_average | 9.934 | 14.362 | 46.62% |
| XGBoost | 3.538 | 4.763 | 16.60% |
| STGCN | 3.123 | 4.288 | 14.65% |

## Test (ID)

| Method | MAE | RMSE | WMAPE |
| --- | ---: | ---: | ---: |
| persistence | 4.561 | 6.040 | 21.48% |
| moving_average | 4.438 | 5.989 | 20.90% |
| historical_average | 10.015 | 14.708 | 47.16% |
| XGBoost | 3.545 | 4.737 | 16.69% |
| STGCN | 3.169 | 4.351 | 14.92% |

## Test (OOD)

| Method | MAE | RMSE | WMAPE |
| --- | ---: | ---: | ---: |
| persistence | 4.480 | 6.033 | 21.29% |
| moving_average | 4.328 | 5.922 | 20.56% |
| historical_average | 12.624 | 18.637 | 59.98% |
| XGBoost | 3.792 | 5.099 | 18.02% |
| STGCN | 3.722 | 5.108 | 17.68% |

Note: raw MAPE is preserved in the CSV but not highlighted here because targets near zero make it unstable; use MAE, RMSE, and WMAPE for comparison.
