import json
import tempfile
import unittest
from pathlib import Path

from simulation.sumo.vehicle_profiles import (
    VehicleProfileError,
    load_vehicle_profiles,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "data" / "maps" / "sumo" / "vehicle_profiles.json"


class VehicleProfileTests(unittest.TestCase):
    def test_default_passenger_profile_has_fuel_and_emission_parameters(self):
        profile = load_vehicle_profiles(PROFILES)["passenger"]
        self.assertEqual(profile.powertrain, "gasoline")
        self.assertEqual(profile.emission_class, "HBEFA3/PC_G_EU4")
        self.assertEqual(profile.fuel_density_mg_per_ml, 745.0)
        self.assertLess(profile.hard_braking_threshold_mps2, 0)
        self.assertEqual(
            profile.sumo_attributes("test")["emissionClass"],
            "HBEFA3/PC_G_EU4",
        )

    def test_invalid_profile_is_rejected(self):
        raw = json.loads(PROFILES.read_text(encoding="utf-8"))
        raw["profiles"]["passenger"]["fuel_density_mg_per_ml"] = 0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(VehicleProfileError, "finite and positive"):
                load_vehicle_profiles(path)


if __name__ == "__main__":
    unittest.main()
