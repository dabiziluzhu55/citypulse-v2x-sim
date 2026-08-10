"""Minimal non-controlling AI observer with frame-gap detection."""

import logging


LOGGER = logging.getLogger(__name__)
_last_frame_id = None


def initialize(metadata: dict) -> None:
    global _last_frame_id
    _last_frame_id = None
    lane_count = sum(
        len(intersection["lanes"])
        for intersection in metadata["intersections"].values()
    )
    LOGGER.info("AI observer initialized with %d lanes", lane_count)


def on_frame(frame: dict) -> None:
    global _last_frame_id
    frame_id = frame["frame_id"]
    if _last_frame_id is not None and frame_id != _last_frame_id + 1:
        LOGGER.warning(
            "AI observer skipped frames %d..%d",
            _last_frame_id + 1,
            frame_id - 1,
        )
    _last_frame_id = frame_id

    for intersection in frame["intersections"].values():
        for lane in intersection["lanes"].values():
            for connection in lane["connection_signal_states"]:
                _ = (
                    connection["movement"],
                    connection["signal_state"],
                    connection["downstream_lane_id"],
                )


def finish(summary: dict) -> None:
    counters = summary["observer_frames"]
    LOGGER.info(
        "AI observer finished: generated=%d consumed=%d dropped=%d",
        counters["generated"],
        counters["consumed"],
        counters["dropped"],
    )
