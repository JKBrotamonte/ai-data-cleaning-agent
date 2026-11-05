"""Cleaner: plan the cleaning, then apply it.

    python -m cleaner_agent.cleaner.agent plan jobs/demo    # model call; plan is test-run in code
    python -m cleaner_agent.cleaner.agent apply jobs/demo   # no model; asks before risky steps

The model gets the intake result, the profile, and a masked sample, and replies
with steps from the fixed list in ops.py. Each plan is test-run on the real file
here; if a step fails, the error (no data values) goes back to the model for
another try. QA failures and revision requests are fed in the same way.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from cleaner_agent import approval, common
from cleaner_agent.cleaner import ops

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def safe_stats(changes: list) -> list:
    """Change log for the model: counts only; example values reduced to shapes."""
    out = []
    for c in changes:
        stats = dict(c["stats"])
        if "unparsed_examples" in stats:
            stats["unparsed_examples"] = [common.shape(v) for v in stats["unparsed_examples"]]
        out.append({"step": c["step"], "op": c["op"], "stats": stats})
    return out


def risky_steps(changes: list, input_rows: int, cfg: dict) -> list:
    """Steps that delete or change the meaning of data. These need a yes before apply."""
    reasons = {
        "drop_columns": lambda a, s: f"deletes column(s) {a.get('columns')}",
        "filter_rows": lambda a, s: f"deletes {s['rows_removed']} row(s) ({a.get('action', 'drop')} where "
                                    f"{a.get('column')} {a.get('condition')} {a.get('value', '')})".replace(" )", ")"),
        "fill_missing": lambda a, s: f"fills {s.get('cells_filled', 0)} blank(s) in {a.get('columns')} "
                                     f"using {a.get('strategy')} {a.get('value', '')}".strip(),
        "regex_replace": lambda a, s: f"rewrites {s.get('cells_changed', 0)} cell(s) in {a.get('columns')} by pattern",
        "merge_columns": lambda a, s: f"replaces {a.get('columns')} with merged column '{a.get('into')}'",
        "replace_values": lambda a, s: f"remaps {s.get('cells_changed', 0)} value(s) in {a.get('columns')}: {a.get('mapping')}",
        "split_column": lambda a, s: f"replaces '{a.get('column')}' with {a.get('into')}",
        "drop_duplicates": lambda a, s: f"deletes {s['rows_removed']} exact duplicate row(s)",
    }
    out = []
    for c in changes:
        op, a, s = c["op"], c["args"], c["stats"]
        if not any(v for k, v in s.items() if isinstance(v, int) and k != "unparsed"):
            continue  # the step changed nothing in this file
        reason = None
        if op == "drop_duplicates" and (a.get("subset") not in (None, "all", "*", []) or a.get("ignore_case_and_spaces")):
            reason = f"deletes {s['rows_removed']} row(s) that are only partly identical"
        elif op in cfg["approval_required_ops"] and op in reasons:
            reason = reasons[op](a, s)
        if input_rows and s.get("rows_removed", 0) / input_rows > cfg["approval_rows_removed_share"]:
            reason = (reason + "; " if reason else "") + f"removes {s['rows_removed']} of {input_rows} rows"
        if reason:
            out.append({"step": c["step"], "op": op, "reason": reason})
    return out


def write_review(path: Path, job: str, result: dict, changes: list) -> None:
    """A readable version of the plan, with steps that need approval flagged."""
    flagged = {r["step"]: r["reason"] for r in result["needs_approval"]}
    lines = [f"# Cleaning plan for {common.job_dir(job).name}", "",
             "Expected results come from a test run on the real file (the original in input/ is never changed). "
             "Steps marked **yes** under Approval delete or change the meaning of data and wait for your y/n.", "",
             "| # | Problem | Columns | Action | Expected result | Reversible | Approval |",
             "|---|---|---|---|---|---|---|"]
    cell = lambda v: str(v).replace("|", "/").replace("\n", " ")
    for c, step in zip(changes, result["plan"]):
        cols = c["args"].get("columns") or c["args"].get("column") or c["args"].get("subset") or \
            list((c["args"].get("mapping") or {}).keys()) or "all"
        stats = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in c["stats"].items() if k != "unparsed_examples" and v)
        reversible = "yes" if c["op"] in ops.REVERSIBLE else "no (original kept)"
        needs = f"**yes**: {flagged[c['step']]}" if c["step"] in flagged else "no"
        lines.append(f"| {c['step']} | {cell(step.get('problem', ''))} | {cell(cols)} | `{c['op']}`: {cell(c['why'])} "
                     f"| {cell(stats or 'no change')} | {reversible} | {cell(needs)} |")
    if result.get("manual_review"):
        lines += ["", "## Check by hand", ""] + [f"- {r}" for r in result["manual_review"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def plan(job: str) -> dict:
    cfg, P = common.load_agent(CONFIG_PATH)
    log = common.get_log(cfg["agent"]["name"])
    request = common.read_request(job)
    intake = common.read_json(common.work(job, "intake.json"))
    if not intake:
        raise SystemExit("Run intake first.")
    files = common.input_files(job)
    sharing = common.ai_sharing(job)
    if sharing == "none":
        raise SystemExit("ai_data_sharing is 'none' for this job, so no data may go to the model.")
    df = common.load_dataset(files[0])
    masked = common.masked_columns(df, sharing)

    payload = {
        "today": str(date.today()),
        "intake": {k: intake.get(k) for k in ("summary", "requirements", "required_output_columns",
                                               "output_format", "assumptions", "risks")},
        "answers": request["sections"].get("answers", ""),
        "revision_requests": request["sections"].get("revision requests", ""),
        "profile": common.read_json(common.work(job, "profile.json")),
        "sample_rows": common.sample_rows(df, masked, cfg["sample_rows"]),
        "max_steps": cfg["max_steps"],
    }
    previous = common.read_json(common.work(job, "plan.json"))
    if previous:
        payload["previous_plan"] = previous.get("plan")
    qa = common.read_json(common.work(job, "qa-report.json"))
    if qa and not qa.get("passed") and qa.get("changes_hash") == common.file_hash(common.work(job, "changes.json")):
        payload["qa_failures_of_previous_plan"] = qa.get("errors", [])

    system = P["system_prompt"].read_text(encoding="utf-8")
    messages = json.dumps(payload, indent=1, default=str)
    result, changes = None, None
    for attempt in range(1, cfg["max_fix_attempts"] + 2):
        result = common.ask_model(cfg, P, system, messages, "plan", common.work(job, "plan.raw.txt"))
        try:
            _, changes = ops.run_plan(df, result.get("plan"), cfg["max_steps"])
            break
        except ops.PlanError as e:
            log.warning("plan test-run failed: %s", e)
            if attempt > cfg["max_fix_attempts"]:
                raise SystemExit(f"The plan still fails after {attempt} tries: {e}")
            messages += f"\n\nYour plan failed when test-run: {e}\nReturn a corrected full reply."

    inputs_hash = common.plan_hash(job)
    result["inputs_hash"] = inputs_hash
    result["attempt"] = previous.get("attempt", 0) + 1 if previous.get("inputs_hash") == inputs_hash else 1
    result["test_run"] = safe_stats(changes)
    result["input_hash"] = common.file_hash(files[0])
    result["needs_approval"] = risky_steps(changes, len(df), cfg)
    common.write_json(common.work(job, "plan.json"), result)
    write_review(common.work(job, "plan-review.md"), job, result, changes)
    log.info("DONE plan steps=%d attempt=%d needs_approval=%d",
             len(result["plan"]), result["attempt"], len(result["needs_approval"]))
    print(f"Plan: {len(result['plan'])} steps (attempt {result['attempt']}). Review: {common.work(job, 'plan-review.md')}")
    for step in result["plan"]:
        print(f"  - {step['op']}: {step['why']}")
    return result


def apply(job: str, assume_yes: bool = False) -> dict:
    cfg, P = common.load_agent(CONFIG_PATH)
    log = common.get_log(cfg["agent"]["name"])
    plan_path = common.work(job, "plan.json")
    plan_data = common.read_json(plan_path)
    if not plan_data:
        raise SystemExit("Run the plan step first.")
    if not approval.ask(plan_data, assume_yes):
        raise SystemExit(f"Not applied. Edit request.md (revision requests) and re-plan, or approve the plan.")
    if plan_data.get("needs_approval"):
        plan_data["approved"] = approval.plan_fingerprint(plan_data)
        common.write_json(plan_path, plan_data)

    intake = common.read_json(common.work(job, "intake.json"))
    source = common.input_files(job)[0]
    if common.file_hash(source) != plan_data.get("input_hash"):
        raise SystemExit(f"{source.name} isn't the file this plan was made for. Re-plan.")
    df = common.load_dataset(source)
    cleaned, changes = ops.run_plan(df, plan_data["plan"], cfg["max_steps"])

    fmt = intake.get("output_format", "same")
    ext = source.suffix.lower() if fmt in ("same", "", None) else f".{fmt}"
    if ext == ".xlsx" and len(cleaned) > common.EXCEL_MAX_ROWS:
        ext = ".csv"
    out = common.work(job, f"cleaned{ext}")
    for old in out.parent.glob("cleaned.*"):
        old.unlink()
    common.write_dataset(cleaned, out)

    record = {
        "plan_hash": common.file_hash(plan_path),
        "source_file": source.name, "output_file": out.name,
        "input_rows": len(df), "output_rows": len(cleaned),
        "input_columns": list(map(str, df.columns)), "output_columns": list(map(str, cleaned.columns)),
        "steps": changes,
    }
    common.write_json(common.work(job, "changes.json"), record)
    log.info("DONE apply rows=%d->%d file=%s", len(df), len(cleaned), out.name)
    print(f"Cleaned: {len(df)} -> {len(cleaned)} rows, saved to {out}")
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "apply"])
    parser.add_argument("job", help="job folder, e.g. jobs/demo")
    parser.add_argument("--yes", action="store_true", help="approve risky steps without asking (benchmarks only)")
    args = parser.parse_args()
    plan(args.job) if args.command == "plan" else apply(args.job, args.yes)


if __name__ == "__main__":
    main()
