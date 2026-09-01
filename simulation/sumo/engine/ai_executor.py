"""Safe execution of backend-generated event-scoped signal plans."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Callable, Mapping, Sequence

from .ai_control import (
    AIControlConfig,
    AIControlPlan,
    AIControlStatus,
    AIControlValidationError,
)
from .events import EventSnapshot
from .signal import SafePhaseController, SignalStage


class AIPlanExecutor:
    """Apply validated target phases without bypassing signal safety rules.

    The executor is deliberately unaware of Qwen, HTTP, RAG, or frontend
    concerns.  It accepts a JSON-like plan from the backend and is the final
    authority before TraCI is changed.
    """

    def __init__(
        self,
        *,
        traci,
        selected_manifest: Mapping[str, Mapping[str, object]],
        controllers: Mapping[str, SafePhaseController],
        baseline_mode: str,
        fixed_state_provider: Callable[[str, float], tuple[int, int | None, str, float]] | None = None,
        config: AIControlConfig | None = None,
        baseline_restore: Callable[[str], None] | None = None,
        baseline_controller: str | None = None,
    ) -> None:
        self.traci = traci
        self.selected_manifest = selected_manifest
        self.controllers = dict(controllers)
        self.baseline_mode = str(baseline_mode)
        self.fixed_state_provider = fixed_state_provider
        self.config = config or AIControlConfig()
        self.baseline_restore = baseline_restore
        self.baseline_controller = str(baseline_controller or self.baseline_mode)
        self._plan: AIControlPlan | None = None
        self._plan_started_at: float | None = None
        self._event_id: str | None = None
        self._recovery_intersections: set[str] = set()
        self._recovery_started_at: float | None = None
        self._recovery_sample_count = 0
        self._baseline_return_pending = False
        self._status = AIControlStatus(
            baseline_controller=self.baseline_controller,
        )

    @property
    def status(self) -> AIControlStatus:
        return self._status

    @property
    def controlled_intersections(self) -> frozenset[str]:
        if self._plan is None or self._status.state != "ACTIVE":
            return frozenset()
        return frozenset(self._plan.controlled_intersections)

    @property
    def override_intersections(self) -> frozenset[str]:
        """Intersections temporarily withheld from the baseline controller."""

        values = set(self._recovery_intersections)
        values.update(self.controlled_intersections)
        return frozenset(values)

    def snapshot_controllers(self) -> Mapping[str, SafePhaseController]:
        """Controllers whose state should override the fixed tracker snapshot."""

        if self.baseline_mode == "algorithm":
            return self.controllers
        ids = set(self._recovery_intersections)
        ids.update(self.controlled_intersections)
        return {
            intersection_id: self.controllers[intersection_id]
            for intersection_id in sorted(ids)
            if intersection_id in self.controllers
        }

    def observe_events(
        self,
        events: Sequence[EventSnapshot],
        current_time: float,
    ) -> bool:
        """Update ARMED/recovery state from the authoritative scheduler."""

        ai_events = [
            event
            for event in events
            if bool(event.details.get("ai_control_enabled", False))
        ]
        active = [event for event in ai_events if event.state == "ACTIVE"]
        current = next(
            (event for event in active if event.event_id == self._event_id),
            active[0] if active else None,
        )
        changed = False
        if current is not None:
            if (
                self._event_id is not None
                and current.event_id != self._event_id
                and self._status.state in {"ARMED", "ACTIVE", "FALLBACK"}
            ):
                # A second AI event waits until the previous takeover has
                # safely returned to baseline, including its recovery window.
                self._start_recovery(float(current_time), reason="event_changed")
                return True
            if self._status.state in {"INACTIVE", "FINISHED"}:
                self._event_id = current.event_id
                self._status = replace(
                    self._status,
                    state="ARMED",
                    ai_enabled=True,
                    active_event_id=current.event_id,
                    last_error=None,
                    fallback_reason=None,
                )
                changed = True
        elif self._event_id is not None and self._status.state in {
            "ARMED",
            "ACTIVE",
            "FALLBACK",
        }:
            self._start_recovery(float(current_time))
            changed = True
        return changed

    def install_from_payload(
        self,
        payload: object,
        *,
        current_time: float,
        events: Sequence[EventSnapshot],
    ) -> None:
        if not isinstance(payload, Mapping):
            raise AIControlValidationError("AI plan command must be an object.")
        event_id = str(payload.get("event_id", "")).strip()
        if not event_id:
            raise AIControlValidationError("AI plan command requires event_id.")
        event = next(
            (
                item
                for item in events
                if item.event_id == event_id and item.state == "ACTIVE"
            ),
            None,
        )
        if event is None:
            raise AIControlValidationError(
                f"AI plan event {event_id!r} is not active."
            )
        if not bool(event.details.get("ai_control_enabled", False)):
            raise AIControlValidationError(
                f"AI plan event {event_id!r} is not AI-enabled."
            )
        if self._status.state == "RECOVERY":
            raise AIControlValidationError(
                "AI plan cannot be installed while the previous takeover is recovering."
            )
        if self._status.state == "FALLBACK" and self._recovery_intersections:
            raise AIControlValidationError(
                "AI plan cannot be installed until baseline recovery is complete."
            )
        if self._event_id is not None and self._event_id != event_id:
            raise AIControlValidationError(
                "AI plan event does not match the current takeover event."
            )
        plan = AIControlPlan.from_mapping(
            payload.get("plan"), config=self.config
        )
        raw_allowed_scope = payload.get("allowed_scope", ())
        if not isinstance(raw_allowed_scope, Sequence) or isinstance(
            raw_allowed_scope, (str, bytes)
        ):
            raise AIControlValidationError("allowed_scope must be an array.")
        allowed_values = [str(item).strip() for item in raw_allowed_scope]
        if any(not item for item in allowed_values):
            raise AIControlValidationError("allowed_scope cannot contain empty IDs.")
        if len(allowed_values) != len(set(allowed_values)):
            raise AIControlValidationError("allowed_scope must not contain duplicates.")
        allowed_scope = tuple(allowed_values)
        unknown_scope = set(allowed_scope) - set(self.selected_manifest)
        if unknown_scope:
            raise AIControlValidationError(
                f"allowed_scope contains unknown intersections: {sorted(unknown_scope)}."
            )
        phase_orders = {
            intersection_id: controller.phase_order
            for intersection_id, controller in self.controllers.items()
        }
        plan.validate_runtime(
            allowed_scope=allowed_scope,
            phase_orders=phase_orders,
        )
        raw_plan_id = str(payload.get("plan_id", "")).strip()
        if not raw_plan_id:
            raise AIControlValidationError("AI plan command requires plan_id.")
        try:
            plan_started_at = float(payload.get("plan_started_at", current_time))
        except (TypeError, ValueError) as exc:
            raise AIControlValidationError("plan_started_at must be numeric.") from exc
        if not math.isfinite(plan_started_at):
            raise AIControlValidationError("plan_started_at must be finite.")
        if plan_started_at > float(current_time) + 1e-6:
            raise AIControlValidationError("plan_started_at cannot be in the future.")
        if raw_plan_id == self._status.plan_id:
            raise AIControlValidationError("AI plan has already been installed.")

        # A fixed-time baseline has no SafePhaseController.  Its controller is
        # created by the session worker and adopts the actual stage here.
        # The fixed tracker intentionally stops reading intersections while AI
        # owns them.  On a later 30-second replan its value would therefore be
        # stale; keep the live SafePhaseController state instead.  Synchronize
        # from the fixed program only for the first plan (or after a completed
        # fallback restoration).
        should_synchronize_fixed = (
            self._status.state in {"INACTIVE", "ARMED", "FINISHED"}
            or (self._status.state == "FALLBACK" and not self._recovery_intersections)
        )
        if (
            self.baseline_mode == "fixed"
            and self.fixed_state_provider is not None
            and should_synchronize_fixed
        ):
            for intersection_id in plan.controlled_intersections:
                phase, pending, stage, stage_elapsed = self.fixed_state_provider(
                    intersection_id, float(current_time)
                )
                del pending
                controller = self.controllers[intersection_id]
                controller.synchronize(
                    current_phase=phase,
                    stage=SignalStage(str(stage)),
                    current_time=float(current_time),
                    stage_elapsed=stage_elapsed,
                )

        self._plan = plan
        self._plan_started_at = plan_started_at
        self._event_id = event_id
        sequence = self._status.plan_sequence + 1
        self._recovery_intersections.clear()
        self._recovery_started_at = None
        self._recovery_sample_count = 0
        self._baseline_return_pending = False
        self._status = replace(
            self._status,
            state="ACTIVE" if not plan.fallback_to_baseline else "FALLBACK",
            ai_enabled=True,
            active_event_id=event_id,
            allowed_scope=allowed_scope,
            controlled_intersections=plan.controlled_intersections,
            plan_sequence=sequence,
            plan_id=raw_plan_id,
            plan_started_at=(None if plan.fallback_to_baseline else plan_started_at),
            plan_valid_until=(
                None
                if plan.fallback_to_baseline
                else plan_started_at + plan.valid_seconds
            ),
            recovery_deadline=None,
            last_error=None,
            fallback_reason=("model_requested_baseline" if plan.fallback_to_baseline else None),
            last_objective=plan.objective,
            last_reason=plan.reason,
            rag_status=(
                None
                if payload.get("rag_status") is None
                else str(payload.get("rag_status"))
            ),
        )
        if plan.fallback_to_baseline:
            self._plan = None

    def mark_fallback(
        self,
        *,
        event_id: str,
        reason: str,
        current_time: float,
        rag_status: str | None = None,
    ) -> None:
        if not str(event_id).strip():
            raise AIControlValidationError("AI fallback requires event_id.")
        controlled = set(self.controlled_intersections) | set(
            self._recovery_intersections
        )
        self._event_id = event_id
        self._plan = None
        # Algorithm baselines share the same SafePhaseController, so they can
        # resume immediately after the failed AI plan.  Fixed-time baselines
        # need a safe controller transition before their original program is
        # restored.
        self._recovery_intersections = (
            controlled if self.baseline_mode == "fixed" else set()
        )
        self._recovery_started_at = float(current_time) if self._recovery_intersections else None
        self._recovery_sample_count = 0
        self._baseline_return_pending = bool(self._recovery_intersections)
        self._status = replace(
            self._status,
            state="FALLBACK",
            ai_enabled=True,
            active_event_id=event_id,
            controlled_intersections=tuple(sorted(self._recovery_intersections)),
            last_error=reason,
            fallback_reason=reason,
            rag_status=rag_status,
            plan_started_at=None,
            plan_valid_until=None,
        )

    def release(self, *, reason: str, current_time: float) -> None:
        if self._event_id is None:
            self._status = replace(
                self._status,
                state="INACTIVE",
                ai_enabled=False,
                last_error=None,
                fallback_reason=None,
            )
            return
        self._start_recovery(float(current_time), reason=reason)

    def advance(self, current_time: float) -> bool:
        """Advance active/recovery controllers and apply safe RYG states."""

        now = float(current_time)
        changed = False
        target_ids = set(self._recovery_intersections)
        target_ids.update(self.controlled_intersections)
        for intersection_id in sorted(target_ids):
            controller = self.controllers.get(intersection_id)
            if controller is None:
                continue
            if controller.advance(now):
                self._apply_controller_state(intersection_id, controller)
                changed = True
        if self._status.state == "RECOVERY":
            self._update_recovery_samples(now)
            ready = all(
                self.controllers[intersection_id].stage == SignalStage.GREEN
                for intersection_id in self._recovery_intersections
                if intersection_id in self.controllers
            )
            deadline = self._status.recovery_deadline
            enough_clear_samples = (
                self._recovery_sample_count >= self.config.recovery_clear_samples
            )
            if ready and (
                enough_clear_samples
                or (deadline is not None and now >= deadline)
            ):
                self._finish_recovery()
                changed = True
        elif self._status.state == "FALLBACK" and self._baseline_return_pending:
            ready = all(
                self.controllers[intersection_id].stage == SignalStage.GREEN
                for intersection_id in self._recovery_intersections
                if intersection_id in self.controllers
            )
            if ready and self._restore_baseline():
                changed = True
        return changed

    def apply_slot(self, current_time: float) -> bool:
        if self._plan is None or self._status.state != "ACTIVE":
            return False
        if self._plan_started_at is None:
            return False
        elapsed = float(current_time) - self._plan_started_at
        if elapsed < -1e-6 or elapsed >= self._plan.valid_seconds:
            return False
        slot = min(
            self.config.slot_count - 1,
            max(0, int(elapsed // self.config.slot_seconds)),
        )
        changed = False
        for intersection_id in self._plan.controlled_intersections:
            controller = self.controllers[intersection_id]
            target = self._plan.signal_plan[intersection_id][slot]
            if controller.request_phase(target, float(current_time)):
                self._apply_controller_state(intersection_id, controller)
                changed = True
        return changed

    def plan_expired(self, current_time: float) -> bool:
        return bool(
            self._status.state == "ACTIVE"
            and self._status.plan_valid_until is not None
            and float(current_time) >= self._status.plan_valid_until - 1e-6
        )

    def _start_recovery(self, current_time: float, *, reason: str = "event_completed") -> None:
        controlled = set(self.controlled_intersections) | set(
            self._recovery_intersections
        )
        self._recovery_intersections = controlled
        self._recovery_started_at = float(current_time)
        self._recovery_sample_count = 0
        self._baseline_return_pending = False
        self._plan = None
        self._status = replace(
            self._status,
            state="RECOVERY",
            active_event_id=self._event_id,
            controlled_intersections=tuple(sorted(controlled)),
            plan_started_at=None,
            plan_valid_until=None,
            recovery_deadline=current_time + self.config.recovery_seconds,
            fallback_reason=reason,
        )

    def _finish_recovery(self) -> None:
        if not self._restore_baseline():
            return
        self._recovery_intersections.clear()
        self._plan = None
        self._event_id = None
        self._recovery_started_at = None
        self._recovery_sample_count = 0
        self._status = replace(
            self._status,
            state="FINISHED",
            ai_enabled=False,
            active_event_id=None,
            allowed_scope=(),
            controlled_intersections=(),
            plan_id=None,
            plan_started_at=None,
            plan_valid_until=None,
            recovery_deadline=None,
        )

    def _restore_baseline(self) -> bool:
        if self.baseline_restore is None:
            self._baseline_return_pending = False
            return True
        for intersection_id in sorted(self._recovery_intersections):
            try:
                self.baseline_restore(intersection_id)
            except Exception:
                # Keep the fixed-time controller in charge until restoring the
                # original program succeeds; never silently drop the override.
                self._baseline_return_pending = True
                self._status = replace(
                    self._status,
                    state="FALLBACK",
                    last_error="baseline_restore_failed",
                    fallback_reason="baseline_restore_failed",
                )
                return False
        self._baseline_return_pending = False
        self._recovery_intersections.clear()
        if self._status.state == "FALLBACK":
            self._status = replace(
                self._status,
                controlled_intersections=(),
                plan_started_at=None,
                plan_valid_until=None,
            )
        return True

    def _update_recovery_samples(self, current_time: float) -> None:
        if self._recovery_started_at is None:
            return
        elapsed = max(0.0, float(current_time) - self._recovery_started_at)
        completed = int(elapsed // self.config.slot_seconds)
        if completed > self._recovery_sample_count:
            self._recovery_sample_count = completed

    def _apply_controller_state(
        self,
        intersection_id: str,
        controller: SafePhaseController,
    ) -> None:
        from .run import _apply_controller_state

        _apply_controller_state(
            self.traci,
            self.selected_manifest[intersection_id],
            controller,
        )


__all__ = ["AIPlanExecutor"]
