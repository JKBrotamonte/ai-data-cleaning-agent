"""Each step's monthly cap counts only that step's spend."""

import tempfile
import unittest
from pathlib import Path

from cleaner_agent import costs


class Caps(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "costs.csv"
        costs.append(self.path, "cleaner", "plan", 1000, 500, 5.0)
        costs.append(self.path, "qa", "report", 100, 50, 0.5)

    def test_spend_is_per_step(self):
        self.assertEqual(costs.month_spend(self.path, "cleaner"), 5.0)
        self.assertEqual(costs.month_spend(self.path, "qa"), 0.5)
        self.assertEqual(costs.month_spend(self.path), 5.5)

    def test_another_steps_spend_does_not_block(self):
        costs.check_budget(self.path, "qa", 2.0)  # total is $5.50, but qa has spent only $0.50

    def test_cap_stops_the_step(self):
        with self.assertRaises(SystemExit):
            costs.check_budget(self.path, "cleaner", 5.0)


if __name__ == "__main__":
    unittest.main()
