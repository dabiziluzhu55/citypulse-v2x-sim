# CV Joint V1 deployment closure

`cv_joint_v1` is an explicit, eval-only generation-1 candidate. It is the new `xiongan_20` default; the existing east/west presets continue to select their explicit legacy U24 alias because this candidate requires the full 20-TLS metadata set. Its Road
decision is the existing frozen product IPPO model; the Cloud and Vehicle
actors use the pinned CV Joint V1 checkpoint. The candidate accepts the
complete `demo_1` through `demo_20` metadata set and preserves the live
movement catalog in its manifest.

The runtime keeps the audited CV `MessageBus` and `PermissionBook` semantics.
`communication.newbridge.CVJointV1EventBridge` mirrors emitted records into the
product `cov2x.v2x.event_batch` shape, the cursor-safe drain, and an optional
sink. Each inline batch is cursor-based and includes all newly emitted events, even when a held message was born in an earlier callback; each event retains its origin snapshot. Bridge records keep `message_type="CVJointV1"` and the exact source
`original_kind`; in particular, `speed_permission` remains distinct from
Road/Cloud priority messages. The bridge is observational and never delivers,
consumes, or changes a control message.

The package contains no `algorithms` imports and no training or optimizer
entrypoint. The generation-1 source container retains training and RNG state for lineage, but this deployment loader reads only the policy state; those fields are intentionally unused. The canonical topology, CV checkpoint, source snapshot hashes, and
frozen IPPO SHA are pinned in
`traffic_control/cov2x/models/cv_joint_v1_manifest.json`. Missing, corrupt, or
identity-mismatched model/topology files fail before initialization.

`step()` returns the normal Protocol 2.0 `actions` plus an inline `v2x` batch.
`traffic_control.cov2x.drain_v2x_events()` and `set_v2x_event_sink()` expose the
same batch/drain/sink ABI. External backend/UI persistence of that field remains
a separate integration task.
