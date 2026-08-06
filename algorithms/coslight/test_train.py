import sys

from algorithms.coslight import controller, train


def test_parse_args_uses_controller_max_green_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["coslight.train"])

    args = train._parse_args()

    assert args.max_green_factor == controller.DEFAULT_MAX_GREEN_FACTOR


def test_parse_args_disables_vehicle_guidance_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["coslight.train"])

    args = train._parse_args()

    assert args.vehicle_guidance == "off"
