"""Read frozen IPPO preferences without replacing its chosen action or records."""
from __future__ import annotations

import numpy as np
import torch


class FrozenRoadHooks:
    def __init__(self, ic):
        self.ic = ic
        self.builder = ic._state_builder
        self.original_choose = ic._choose_action
        self.original_build = self.builder.build_phase_features
        self.active = False
        self.installed = False
        self.iid = None
        self.margins = {}

    def install(self):
        if self.installed:
            return
        if any(getattr(self.ic._choose_action, name, False)
               for name in ("_cv_joint_wrapped", "_j0_wrapped", "_rcv1_wrapped", "_voi_wrapped")):
            raise RuntimeError("another Road wrapper is still installed")

        def build(iid, intersection, **kwargs):
            if not self.active:
                return self.original_build(iid, intersection, **kwargs)
            if self.iid is not None:
                raise RuntimeError("unconsumed TLS identity")
            self.iid = iid
            return self.original_build(iid, intersection, **kwargs)

        def choose(state, phase_features, mask, *args, **kwargs):
            if not self.active:
                return self.original_choose(state, phase_features, mask, *args, **kwargs)
            iid = self.iid
            self.iid = None
            if iid is None:
                raise RuntimeError("IPPO decision without captured TLS identity")
            result = self.original_choose(state, phase_features, mask, *args, **kwargs)
            model = self.ic._model
            device = getattr(self.ic, "_device", "cpu")
            with torch.no_grad():
                raw = model.actor_forward(torch.as_tensor(state, dtype=torch.float32, device=device)[None],
                                          torch.as_tensor(phase_features, dtype=torch.float32, device=device)[None])
                logits = raw.squeeze(0).detach().cpu().numpy()
            legal = np.asarray(mask, dtype=bool)
            if logits.shape != legal.shape or not np.isfinite(logits[legal]).all():
                raise ValueError("invalid frozen-IPPO margin observation")
            ranked = np.sort(logits[legal])
            self.margins[iid] = float(ranked[-1] - ranked[-2]) if len(ranked) >= 2 else 0.
            return result

        choose._cv_joint_wrapped = True
        self.choose_wrapper = choose
        self.build_wrapper = build
        self.ic._choose_action = choose
        self.builder.build_phase_features = build
        self.active = True
        self.installed = True

    def close(self):
        self.active = False
        self.iid = None
        if not self.installed:
            return
        changed = []
        if self.ic._choose_action is self.choose_wrapper:
            self.ic._choose_action = self.original_choose
        else:
            changed.append("choose")
        if self.builder.build_phase_features is self.build_wrapper:
            self.builder.build_phase_features = self.original_build
        else:
            changed.append("builder")
        self.installed = False
        if changed:
            raise RuntimeError("Road hook ownership changed: " + ",".join(changed))
