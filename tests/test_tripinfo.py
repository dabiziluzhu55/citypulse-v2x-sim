import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from simulation.sumo.tripinfo import TripInfoError, load_tripinfo_totals


class TripInfoTests(unittest.TestCase):
    def test_aggregates_arrived_unfinished_and_fuel_by_vehicle_density(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tripinfo.xml"
            path.write_text(
                """<tripinfos>
  <tripinfo id="arrived" vType="petrol" arrival="12.5">
    <emissions fuel_abs="745"/>
  </tripinfo>
  <tripinfo id="unfinished" vType="diesel" arrival="-1">
    <emissions fuel_abs="832"/>
  </tripinfo>
  <tripinfo id="event" vType="citypulse_disturbance_vehicle" arrival="4">
    <emissions fuel_abs="9999"/>
  </tripinfo>
</tripinfos>""",
                encoding="utf-8",
            )
            totals = load_tripinfo_totals(
                path,
                {
                    "petrol": SimpleNamespace(fuel_density_mg_per_ml=745.0),
                    "diesel": SimpleNamespace(fuel_density_mg_per_ml=832.0),
                },
            )

        self.assertEqual(totals.departed_vehicles, 2)
        self.assertEqual(totals.arrived_vehicles, 1)
        self.assertEqual(totals.fuel_consumed_mg, 1577.0)
        self.assertEqual(totals.fuel_consumed_ml, 2.0)

    def test_rejects_tripinfo_without_emissions_device_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tripinfo.xml"
            path.write_text(
                '<tripinfos><tripinfo id="car" vType="petrol" arrival="1"/></tripinfos>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TripInfoError, "emissions device"):
                load_tripinfo_totals(
                    path,
                    {"petrol": SimpleNamespace(fuel_density_mg_per_ml=745.0)},
                )


if __name__ == "__main__":
    unittest.main()
