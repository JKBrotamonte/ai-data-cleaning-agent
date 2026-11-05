"""One command for the whole pipeline:

    python -m cleaner_agent run jobs/demo          # intake -> plan -> your y/n -> apply -> QA
    python -m cleaner_agent run jobs/demo --yes    # approve risky steps without asking (benchmarks)

Steps whose inputs haven't changed are skipped, so a rerun after answering intake
questions or adding revision requests only redoes what's needed. A QA failure goes back
to the cleaner as a new plan, up to --max-plans plans in all.
"""

import argparse

from cleaner_agent import common, costs
from cleaner_agent.cleaner import agent as cleaner
from cleaner_agent.intake import agent as intake
from cleaner_agent.llm import COSTS_FILE
from cleaner_agent.qa import agent as qa


def plan_is_current(job: str) -> bool:
    plan = common.read_json(common.work(job, "plan.json"))
    if not plan or plan.get("inputs_hash") != common.plan_hash(job):
        return False
    report = common.read_json(common.work(job, "qa-report.json"))
    changes = common.work(job, "changes.json")
    failed_this_plan = report and not report.get("passed") and \
        report.get("changes_hash") == common.file_hash(changes) and \
        common.read_json(changes).get("plan_hash") == common.file_hash(common.work(job, "plan.json"))
    return not failed_this_plan


def run(job: str, assume_yes: bool = False, max_plans: int = 3) -> dict:
    spent_before = costs.month_spend(COSTS_FILE)

    intake_data = common.read_json(common.work(job, "intake.json"))
    if intake_data.get("inputs_hash") != common.intake_hash(job):
        print("== Intake")
        intake.run(job)
    blockers = common.intake_blockers(job)
    if blockers:
        print("\nStopped before cleaning. Settle these, then run again:")
        for b in blockers:
            print(f"  - {b}")
        print(f"Write answers under '## Answers' in {common.job_dir(job) / 'request.md'}, "
              "or set 'proceed: yes' to clean anyway.")
        return {"passed": False, "stopped": "intake"}

    report = common.read_json(common.work(job, "qa-report.json"))
    changes = common.read_json(common.work(job, "changes.json"))
    if plan_is_current(job) and report.get("passed") and \
            report.get("changes_hash") == common.file_hash(common.work(job, "changes.json")) and \
            changes.get("plan_hash") == common.file_hash(common.work(job, "plan.json")) and \
            (common.job_dir(job) / "output").exists():
        print(f"Already done and nothing changed. Output: {common.job_dir(job) / 'output'}")
        return {**report, "cost_usd": 0.0}

    report = {}
    for attempt in range(1, max_plans + 1):
        if not plan_is_current(job):
            print(f"\n== Plan (try {attempt} of {max_plans})")
            cleaner.plan(job)
        print("\n== Apply")
        cleaner.apply(job, assume_yes)
        print("\n== QA")
        report = qa.run(job)
        if report["passed"]:
            break
        if attempt == max_plans:
            print(f"\nQA still fails after {max_plans} plans. Read {common.work(job, 'qa-report.md')} "
                  "and add a revision request, or clean those parts by hand.")

    spent = costs.month_spend(COSTS_FILE) - spent_before
    print(f"\nModel cost for this run: ${spent:.4f}")
    report["cost_usd"] = round(spent, 4)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m cleaner_agent")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run the whole pipeline on one job folder")
    r.add_argument("job", help="job folder, e.g. jobs/demo")
    r.add_argument("--yes", action="store_true", help="approve risky steps without asking (benchmarks only)")
    r.add_argument("--max-plans", type=int, default=3, help="plans to try when QA fails (default 3)")
    args = parser.parse_args()
    report = run(args.job, args.yes, args.max_plans)
    raise SystemExit(0 if report.get("passed") else 1)


if __name__ == "__main__":
    main()
