"""The run command: stops on intake questions, re-plans when QA fails, skips steps that are current.
Fake model; offline and free."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cleaner_agent import __main__ as runner
from cleaner_agent import common

INTAKE = {"summary": "Dedupe a list", "requirements": [], "required_output_columns": [], "output_format": "same",
          "assumptions": [], "questions": [], "risks": [], "requested_no_ai": False}
BAD = {"plan": [{"op": "drop_duplicates", "why": "repeats"}, {"op": "trim_whitespace", "why": "spaces"}]}
GOOD = {"plan": [{"op": "trim_whitespace", "why": "spaces"}, {"op": "drop_duplicates", "why": "repeats"}]}


class Model:
    def __init__(self, plans, intake=INTAKE):
        self.plans, self.intake, self.calls, self.users = list(plans), intake, [], []

    def __call__(self, cfg, P, system, user, label, raw_path):
        self.calls.append(label)
        self.users.append(user)
        if label == "intake":
            return self.intake
        if label == "plan":
            return self.plans.pop(0)
        return {"report_summary": "done", "notes": []}


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        job = self.tmp / "dupes"
        (job / "input").mkdir(parents=True)
        (job / "input" / "list.csv").write_text("item,qty\nx ,1\nx,1\ny,2\n", encoding="utf-8")
        (job / "request.md").write_text("ai_data_sharing: pii_masked\n\n## Requirements\nRemove duplicates\n",
                                        encoding="utf-8")
        self.job = str(job)

    def run_with(self, model):
        with mock.patch.object(common, "ask_model", model), mock.patch("builtins.input", return_value="y"), \
                mock.patch.object(runner.costs, "month_spend", return_value=0.0):
            return runner.run(self.job)

    def test_qa_failure_triggers_a_new_plan_with_the_failure(self):
        model = Model([BAD, GOOD])
        report = self.run_with(model)
        self.assertTrue(report["passed"])
        self.assertEqual(model.calls, ["intake", "plan", "plan", "report"])
        self.assertIn("No duplicates left after dedupe", model.users[2])  # the 2nd plan sees why the 1st failed

    def test_gives_up_after_max_plans(self):
        model = Model([BAD, BAD, BAD])
        report = self.run_with(model)
        self.assertFalse(report["passed"])
        self.assertEqual(model.calls.count("plan"), 3)
        self.assertFalse((Path(self.job) / "output").exists())

    def test_stops_on_intake_questions_without_planning(self):
        model = Model([GOOD], intake={**INTAKE, "questions": ["Keep the first or last duplicate?"]})
        report = self.run_with(model)
        self.assertEqual(report.get("stopped"), "intake")
        self.assertEqual(model.calls, ["intake"])

    def test_rerun_skips_current_steps(self):
        self.run_with(Model([GOOD]))
        model = Model([])
        report = self.run_with(model)
        self.assertTrue(report["passed"])
        self.assertEqual(model.calls, [])  # nothing changed, so no model calls at all

    def test_revision_request_replans(self):
        self.run_with(Model([GOOD]))
        request = Path(self.job) / "request.md"
        request.write_text(request.read_text(encoding="utf-8") + "\n## Revision requests\nSort by item\n",
                           encoding="utf-8")
        model = Model([GOOD])
        self.run_with(model)
        self.assertEqual(model.calls, ["plan", "report"])  # intake is still current; the plan isn't


if __name__ == "__main__":
    unittest.main()
