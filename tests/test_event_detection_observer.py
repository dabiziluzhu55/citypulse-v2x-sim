import os
import unittest
from unittest.mock import patch

from algorithms.event_detection.ai_observer import _config_from_env


class EventDetectionObserverConfigTests(unittest.TestCase):
    def test_accident_detection_is_explicitly_opt_in(self):
        with patch.dict(os.environ, {"EVENT_DETECTION_ENABLE_ACCIDENT": "true"}):
            self.assertTrue(_config_from_env().enable_accident)
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_config_from_env().enable_accident)


if __name__ == "__main__":
    unittest.main()
