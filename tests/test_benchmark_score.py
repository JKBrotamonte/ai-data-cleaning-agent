"""The benchmark scorer is right at both ends: the clean truth scores 100% with nothing broken,
the untouched dirty file scores 0% with nothing broken, and a damaged file is caught."""

import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from benchmark import generate
from benchmark.score import score


class Scorer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        generate.JOBS = cls.tmp
        generate.main()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_truth_is_perfect_and_dirty_is_zero(self):
        for name in generate.DATASETS:
            job = self.tmp / name
            perfect, untouched = score(job, job / "truth.csv"), score(job, job / "input" / f"{name}.csv")
            self.assertEqual(perfect["fixed"], perfect["total"], name)
            self.assertEqual((perfect["broke_cells"], perfect["missing_rows"]), (0, 0), name)
            self.assertEqual(untouched["fixed"], 0, name)
            self.assertEqual(untouched["broke_cells"], 0, name)

    def test_damage_is_counted(self):
        job = self.tmp / "orders"
        with open(job / "truth.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        rows[1][3] = "Wrong"          # a clean cell changed
        del rows[2]                   # a real row dropped
        damaged = self.tmp / "damaged.csv"
        with open(damaged, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        s = score(job, damaged)
        self.assertEqual(s["missing_rows"], 1)
        self.assertGreaterEqual(s["broke_cells"], 1)


if __name__ == "__main__":
    unittest.main()
