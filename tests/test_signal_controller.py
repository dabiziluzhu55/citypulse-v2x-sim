import unittest

from simulation.sumo.controller import (
    InvalidPhaseAction,
    SafePhaseController,
    SignalStage,
)


class SafePhaseControllerTests(unittest.TestCase):
    def make_controller(self):
        return SafePhaseController(
            (1, 2, 3, 4),
            {value: (3.0, 2.0) for value in (1, 2, 3, 4)},
            minimum_green=5.0,
        )

    def test_enforces_complete_transition(self):
        controller = self.make_controller()
        controller.request_phase(2, 1.0)
        controller.advance(4.999)
        self.assertEqual(controller.stage, SignalStage.GREEN)
        self.assertEqual(controller.current_phase, 1)

        controller.advance(5.0)
        self.assertEqual(controller.stage, SignalStage.YELLOW)
        controller.advance(7.999)
        self.assertEqual(controller.stage, SignalStage.YELLOW)
        controller.advance(8.0)
        self.assertEqual(controller.stage, SignalStage.CLEARANCE)
        controller.advance(10.0)
        self.assertEqual(controller.stage, SignalStage.GREEN)
        self.assertEqual(controller.current_phase, 2)

    def test_latest_request_wins_during_transition(self):
        controller = self.make_controller()
        controller.request_phase(2, 5.0)
        self.assertEqual(controller.stage, SignalStage.YELLOW)
        controller.request_phase(3, 6.0)
        controller.advance(10.0)
        self.assertEqual(controller.current_phase, 3)
        self.assertEqual(controller.stage, SignalStage.GREEN)

    def test_same_phase_is_noop(self):
        controller = self.make_controller()
        self.assertFalse(controller.request_phase(1, 1.0))
        self.assertEqual(controller.stage, SignalStage.GREEN)

    def test_invalid_phase_is_rejected_before_transition(self):
        controller = self.make_controller()
        with self.assertRaises(InvalidPhaseAction):
            controller.request_phase(9, 1.0)
        self.assertEqual(controller.current_phase, 1)
        self.assertEqual(controller.stage, SignalStage.GREEN)

    def test_time_cannot_move_backwards(self):
        controller = self.make_controller()
        controller.advance(2.0)
        with self.assertRaises(ValueError):
            controller.advance(1.0)


if __name__ == "__main__":
    unittest.main()

