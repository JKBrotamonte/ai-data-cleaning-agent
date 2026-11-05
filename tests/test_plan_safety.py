"""run_plan rejects anything outside the fixed operation list, and counts what each step changes.
Offline; made-up data."""

import unittest

import pandas as pd

from cleaner_agent.cleaner import ops


def df():
    return pd.DataFrame({"name": [" Ann ", "Bob", "Bob", ""], "city": ["NYC", "n/a", "n/a", ""]})


class PlanRejected(unittest.TestCase):
    def test_unknown_op(self):
        with self.assertRaisesRegex(ops.PlanError, "unknown op 'run_python'"):
            ops.run_plan(df(), [{"op": "run_python", "why": "x", "code": "import os"}], max_steps=10)

    def test_missing_why(self):
        with self.assertRaisesRegex(ops.PlanError, "'why' is required"):
            ops.run_plan(df(), [{"op": "trim_whitespace"}], max_steps=10)

    def test_unknown_column(self):
        with self.assertRaisesRegex(ops.PlanError, "unknown column"):
            ops.run_plan(df(), [{"op": "drop_columns", "columns": ["salary"], "why": "x"}], max_steps=10)

    def test_too_many_steps(self):
        plan = [{"op": "trim_whitespace", "why": "x"}] * 3
        with self.assertRaisesRegex(ops.PlanError, "limit is 2"):
            ops.run_plan(df(), plan, max_steps=2)

    def test_empty_or_wrong_type(self):
        for plan in ([], {"op": "trim_whitespace"}, None):
            with self.assertRaises(ops.PlanError):
                ops.run_plan(df(), plan, max_steps=10)

    def test_bad_regex_rejected(self):
        with self.assertRaisesRegex(ops.PlanError, "bad regex"):
            ops.run_plan(df(), [{"op": "regex_replace", "columns": ["name"], "pattern": "(", "why": "x"}], 10)


class ChangeLog(unittest.TestCase):
    def test_every_step_is_counted(self):
        plan = [{"op": "trim_whitespace", "why": "stray spaces"},
                {"op": "standardize_missing", "why": "n/a means missing"},
                {"op": "drop_duplicates", "why": "exact repeats"},
                {"op": "drop_empty_rows", "why": "blank rows"}]
        out, changes = ops.run_plan(df(), plan, max_steps=10)
        self.assertEqual(list(out["name"]), ["Ann", "Bob"])
        stats = [c["stats"] for c in changes]
        self.assertEqual(stats[0]["cells_changed"], 1)
        self.assertEqual(stats[1]["cells_blanked"], 2)   # 'n/a' -> '' is counted, not silent
        self.assertEqual(stats[2]["rows_removed"], 1)
        self.assertEqual(stats[3]["rows_removed"], 1)

    def test_input_frame_is_not_modified(self):
        original = df()
        ops.run_plan(original, [{"op": "trim_whitespace", "why": "x"}], max_steps=10)
        self.assertEqual(original.loc[0, "name"], " Ann ")


if __name__ == "__main__":
    unittest.main()
