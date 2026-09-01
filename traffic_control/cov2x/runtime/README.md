# Frozen CoV2X deployment runtime

This package is the deployment-owned inference closure for the frozen update-24
candidate. It intentionally imports only `traffic_control.*` plus NumPy,
PyTorch, and the Python standard library.

The source was copied from the authoritative training runtime at the start of
`COV2X_SELF_CONTAINED_V2X_DEPLOYMENT_v1`. Modules other than import-path
rewrites remain byte-equivalent to their training counterparts. Candidate
selection and eval-only enforcement stay in `traffic_control.cov2x.candidates`.

Do not add training scripts, experiment runners, seed handling, backend code, or
frontend code here. Any future candidate must receive its own adapter and
deployment-contract verification instead of silently changing this frozen
runtime.
