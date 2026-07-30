import json
import tempfile
import unittest
from pathlib import Path

from simulation.sumo.vehicle import build_vehicle_type_metadata
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
        self.assertEqual(profile.pcu_factor, 1.0)
        self.assertEqual(profile.emission_class, "HBEFA3/PC_G_EU4")
        self.assertEqual(profile.fuel_density_mg_per_ml, 745.0)
        self.assertLess(profile.hard_braking_threshold_mps2, 0)
        self.assertEqual(
            profile.sumo_attributes("test")["emissionClass"],
            "HBEFA3/PC_G_EU4",
        )
        profiles = load_vehicle_profiles(PROFILES)
        self.assertEqual(profiles["bus"].pcu_factor, 2.0)
        self.assertEqual(profiles["truck"].pcu_factor, 2.5)

    def test_electric_bicycle_profile_reaches_algorithm_metadata(self):
        profiles = load_vehicle_profiles(PROFILES)
        electric_bicycle = profiles["electric_bicycle"]
        self.assertEqual(electric_bicycle.pcu_factor, 0.5)
        self.assertEqual(electric_bicycle.v_class, "bicycle")
        self.assertEqual(electric_bicycle.powertrain, "electric")
        self.assertEqual(electric_bicycle.emission_class, "HBEFA3/zero")
        self.assertEqual(electric_bicycle.max_speed_mps, 6.94)
        self.assertEqual(
            electric_bicycle.sumo_attributes("official_electric_bicycle")[
                "vClass"
            ],
            "bicycle",
        )

        metadata = build_vehicle_type_metadata(
            {"official_electric_bicycle": "electric_bicycle"}, profiles
        )["official_electric_bicycle"]
        self.assertEqual(metadata.profile_id, "electric_bicycle")
        self.assertEqual(metadata.pcu_factor, 0.5)
        self.assertEqual(metadata.vehicle_class, "bicycle")
        self.assertEqual(metadata.powertrain, "electric")
        self.assertEqual(metadata.emission_class, "HBEFA3/zero")
        self.assertEqual(metadata.length_m, 1.8)
        self.assertEqual(metadata.max_speed_mps, 6.94)

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
