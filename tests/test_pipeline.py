"""The whole pipeline on the demo job with a fake model: intake -> plan -> approval -> apply -> QA.
Offline and free; no API key needed."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cleaner_agent import approval, common
from cleaner_agent.cleaner import agent as cleaner
from cleaner_agent.intake import agent as intake
from cleaner_agent.qa import agent as qa

DEMO = common.ROOT / "jobs" / "demo"

PLAN = {"plan": [
    {"op": "trim_whitespace", "why": "Removed extra spaces"},
    {"op": "standardize_missing", "why": "N/A and n/a mean missing"},
    {"op": "drop_empty_rows", "why": "Removed blank rows"},
    {"op": "standardize_dates", "columns": ["signup_date"], "why": "Dates as YYYY-MM-DD"},
    {"op": "standardize_phone", "columns": ["phone"], "why": "One phone format"},
    {"op": "standardize_email", "columns": ["email"], "why": "Lowercased valid emails"},
    {"op": "replace_values", "columns": ["city"], "mapping": {"huston": "Houston"}, "why": "Fixed Huston typo"},
    {"op": "normalize_case", "columns": ["plan"], "case": "title", "why": "Plan names in one case"},
    {"op": "drop_duplicates", "why": "Removed exact repeats"},
], "requirements_coverage": [{"id": "R1", "covered_by_steps": [4], "status": "covered"}], "manual_review": []}

INTAKE = {"summary": "Clean a customer list", "requirements": [{"id": "R1", "text": "Dates as YYYY-MM-DD",
          "source": "request"}], "required_output_columns": ["customer_id", "email"], "output_format": "same",
          "assumptions": [], "questions": [], "risks": [], "requested_no_ai": False}


def fake_model(cfg, P, system, user, label, raw_path):
    fake_model.seen.append(user)
    return {"intake": INTAKE, "plan": PLAN, "report": {"report_summary": "Cleaned 8 rows.", "notes": []}}[label]


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.job = str(self.tmp / "demo")
        shutil.copytree(DEMO, self.job, ignore=shutil.ignore_patterns("work", "output"))
        fake_model.seen = []
        patcher = mock.patch.object(common, "ask_model", fake_model)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def run_all(self, answer="y"):
        intake.run(self.job)
        cleaner.plan(self.job)
        with mock.patch("builtins.input", return_value=answer):
            cleaner.apply(self.job)
        return qa.run(self.job)

    def test_end_to_end_passes_and_writes_output(self):
        report = self.run_all()
        self.assertTrue(report["passed"], report["errors"])
        out = Path(self.job) / "output"
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["cleaning-report.md", "customers-cleaned.csv", "data-dictionary.csv"])
        cleaned = common.load_dataset(out / "customers-cleaned.csv")
        self.assertEqual(len(cleaned), 6)  # 8 rows - 1 blank - 1 duplicate
        self.assertEqual(set(cleaned["city"]), {"Houston", "Dallas", "Austin"})
        self.assertTrue(cleaned["signup_date"].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all())

    def test_model_never_sees_personal_values(self):
        self.run_all()
        sent = "\n".join(fake_model.seen)
        for secret in ("Ana Torres", "ana.torres@example.com", "Ana.Torres@Example.com", "555-0123"):
            self.assertNotIn(secret, sent)

    def test_saying_no_stops_apply_and_original_is_untouched(self):
        before = common.file_hash(Path(self.job) / "input" / "customers.csv")
        intake.run(self.job)
        result = cleaner.plan(self.job)
        self.assertTrue(result["needs_approval"])  # the city remap and the dedupe need a yes
        with mock.patch("builtins.input", return_value="n"), self.assertRaises(SystemExit):
            cleaner.apply(self.job)
        self.assertFalse(common.work(self.job, "cleaned.csv").exists())
        self.assertEqual(before, common.file_hash(Path(self.job) / "input" / "customers.csv"))

    def test_changed_plan_needs_a_new_yes(self):
        intake.run(self.job)
        cleaner.plan(self.job)
        with mock.patch("builtins.input", return_value="y"):
            cleaner.apply(self.job)
        plan = common.read_json(common.work(self.job, "plan.json"))
        self.assertTrue(approval.is_approved(plan))
        plan["plan"].append({"op": "drop_columns", "columns": ["plan"], "why": "x"})
        self.assertFalse(approval.is_approved(plan))

    def test_qa_fails_when_a_row_goes_missing(self):
        self.run_all()
        path = common.work(self.job, "cleaned.csv")
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # silently lose the last row
        report = qa.run(self.job)
        self.assertFalse(report["passed"])
        self.assertTrue(any(e.startswith("Row count adds up") for e in report["errors"]))
        self.assertFalse((Path(self.job) / "output").exists())  # a failed job gets no output


if __name__ == "__main__":
    unittest.main()
