# CoV2X Off-peak Guard V2

This is an opt-in continuation from the immutable deployed checkpoint:
traffic_control/cov2x/models/cov2x_g30_temporary_cap_u24.pt

Design:

- Morning/evening periods keep the existing learned Road/Cloud/Vehicle path.
- Off-peak Road decisions fall back to the exact frozen Strong Max Pressure implementation.
- Off-peak Vehicle advice is emitted and trained only for ADVICE_ELIGIBLE states.
- PPO batches contain morning, off-peak, evening, off-peak episodes.
- Road and Cloud actors remain frozen. Every fifth Vehicle update is saved separately.

Rollback: disable COV2X_OFFPEAK_GUARD_V2 and continue using the existing
cov2x_g30_temporary_cap_u24 deployment alias.
