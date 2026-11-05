"""QA: check the cleaned file in code, then write the output package.

    python -m cleaner_agent.qa.agent jobs/demo

Every check is recorded as pass, warn, or fail. Any fail (rows that don't add up,
unexpected data loss, a changed original, missing required columns, duplicates
or stray spaces left behind) fails the job and goes back to the cleaner for a
new plan; a failed job never gets an output package. Warnings pass but are
listed in the report. Writes work/qa-report.json and work/qa-report.md.
A passing job gets output/: the cleaned file, cleaning-report.md, and
data-dictionary.csv. The model only writes the summary, from counts, never data.
"""

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from cleaner_agent import approval, common
from cleaner_agent.cleaner import ops

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def describe(stats: dict) -> str:
    words = {"cells_changed": "cells changed", "rows_removed": "rows removed", "cells_filled": "blanks filled",
             "columns_removed": "columns removed", "columns_added": "columns added",
             "columns_renamed": "columns renamed", "columns_merged": "columns merged",
             "unparsed": "values left unchanged (couldn't convert)",
             "not_valid": "phone numbers that look unassigned (formatted; worth a check)",
             "rows_with_fewer_parts": "rows had fewer parts than expected"}
    parts = [f"{stats[k]:,} {w}" for k, w in words.items() if stats.get(k)]
    return ", ".join(parts) or "no changes needed"


def column_type(s: pd.Series) -> str:
    vals = s[~common.blank(s)].astype(str)
    if vals.empty:
        return "empty"
    if pd.to_numeric(vals, errors="coerce").notna().all():
        return "number"
    if vals.str.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?").all():
        return "date"
    if vals.str.fullmatch(common.EMAIL.pattern).mean() > 0.9:
        return "email"
    return "text"


def run_checks(job: str, cfg: dict, changes: dict) -> tuple:
    """Returns (checks, cleaned). Check details hold counts and column names only, because
    failures go back to the model."""
    checks = []
    add = lambda name, result, detail="": checks.append({"check": name, "result": result, "detail": detail})
    intake = common.read_json(common.work(job, "intake.json"))
    plan = common.read_json(common.work(job, "plan.json"))
    profile = common.read_json(common.work(job, "profile.json"))
    steps = changes["steps"]
    ops_used = {s["op"] for s in steps}

    source = common.input_files(job)[0]
    unchanged = common.file_hash(source) == plan.get("input_hash")
    add("Original file unchanged", "pass" if unchanged else "fail",
        "" if unchanged else f"{source.name} changed after planning.")
    try:
        cleaned = common.load_dataset(common.work(job, changes["output_file"]))
        add("Output file opens", "pass", changes["output_file"])
    except Exception as e:
        add("Output file opens", "fail", f"couldn't read it back: {type(e).__name__}")
        return checks, None

    removed = sum(s["stats"].get("rows_removed", 0) for s in steps)
    ok = changes["input_rows"] - removed == len(cleaned)
    add("Row count adds up", "pass" if ok else "fail",
        f"{changes['input_rows']} in - {removed} removed by plan steps = {changes['input_rows'] - removed}; file has {len(cleaned)}")
    add("Output has rows", "pass" if len(cleaned) else "fail")
    required = intake.get("required_output_columns") or []
    missing = [c for c in required if c not in cleaned.columns]
    add("Required columns present", "fail" if missing else "pass",
        f"missing {missing}" if missing else (f"{len(required)} required" if required else "none specified"))

    loss = [(s["step"], s["op"], s["stats"]["cells_blanked"]) for s in steps
            if s["stats"].get("cells_blanked") and s["op"] not in ops.BLANKING_OPS]
    hard = [l for l in loss if l[1] not in ("replace_values", "regex_replace", "fill_missing")]
    add("No unexpected data loss", "fail" if hard else "warn" if loss else "pass",
        "; ".join(f"step {n} ({op}) blanked {c} cell(s)" for n, op, c in loss) or
        "only blanking steps (missing markers, whitespace) emptied cells")
    if "drop_duplicates" in ops_used:
        d = int(cleaned.duplicated().sum())
        add("No duplicates left after dedupe", "fail" if d else "pass",
            f"{d} exact duplicate rows remain; move drop_duplicates after the formatting steps" if d else "")
    if "trim_whitespace" in ops_used:
        # load_dataset reads every column as text, so every column is checked
        n = int(cleaned.apply(lambda s: s.map(lambda v: isinstance(v, str) and v != v.strip())).sum().sum())
        add("No stray spaces after trimming", "fail" if n else "pass", f"{n} cells" if n else "")

    share = removed / changes["input_rows"] if changes["input_rows"] else 0
    add("Rows removed within limit", "warn" if share > cfg["max_rows_removed_share"] else "pass",
        f"{removed} of {changes['input_rows']} ({share:.0%})")
    unparsed = [s for s in steps if s["stats"].get("unparsed")]
    add("All targeted values converted", "warn" if unparsed else "pass",
        "; ".join(f"step {s['step']} ({s['op']}): {s['stats']['unparsed']} value(s) in "
                  f"{s['args'].get('columns') or s['args'].get('column')} left as they were" for s in unparsed))
    fake = [s for s in steps if s["stats"].get("not_valid")]
    if fake:
        add("Phone numbers look assigned", "warn", "; ".join(
            f"step {s['step']}: {s['stats']['not_valid']} number(s) in {s['args'].get('columns')} have the right length "
            "but aren't assigned (test or made-up numbers?); formatted anyway" for s in fake))
    short = [s for s in steps if s["stats"].get("rows_with_fewer_parts")]
    if short:
        add("Splits complete", "warn", "; ".join(f"step {s['step']}: {s['stats']['rows_with_fewer_parts']} row(s) "
                                                  "had fewer parts; extra columns blank there" for s in short))
    partial = [r for r in plan.get("requirements_coverage", []) if r.get("status") != "covered"]
    add("Requirements met", "warn" if partial else "pass",
        "; ".join(f"{r.get('id')} {r.get('status')}: {r.get('note', '')}".strip() for r in partial))
    wb = profile.get("workbook", {})
    if wb.get("confirm") or [n for n in wb.get("notes", []) if not n.startswith("Cell formatting")]:
        add("Workbook features preserved", "warn", " ".join(wb.get("confirm", []) + wb.get("notes", [])))
    bad_headers = [str(c) for c in cleaned.columns if str(c).startswith("Unnamed") or not str(c).strip()]
    add("Every column has a header", "warn" if bad_headers else "pass", f"{len(bad_headers)} without" if bad_headers else "")
    formula = int(cleaned.apply(lambda s: s.astype(str).str.match(r"^[=@]")).sum().sum())
    add("No cells that run as formulas", "warn" if formula else "pass",
        f"{formula} cells start with = or @" if formula else "")
    for m in plan.get("manual_review", []):
        add("Check by hand", "warn", m)
    return checks, cleaned


def build_report(job, changes, original, cleaned, notes, text) -> str:
    """cleaning-report.md for whoever uses the cleaned file."""
    plan = common.read_json(common.work(job, "plan.json"))
    intake = common.read_json(common.work(job, "intake.json"))
    reqs = {r["id"]: r["text"] for r in intake.get("requirements", [])}
    blanks = lambda df: int(df.apply(common.blank).sum().sum())
    lines = ["# Data cleaning report", "", text.get("report_summary", ""), "",
             "## Before and after", "", "| | Before | After |", "|---|---|---|",
             f"| Rows | {len(original):,} | {len(cleaned):,} |",
             f"| Columns | {len(original.columns)} | {len(cleaned.columns)} |",
             f"| Exact duplicate rows | {int(original.duplicated().sum()):,} | {int(cleaned.duplicated().sum()):,} |",
             f"| Blank cells | {blanks(original):,} | {blanks(cleaned):,} |",
             "", "## What was done", ""]
    lines += [f"{s['step']}. {s['why']} ({describe(s['stats'])})" for s in changes["steps"]]
    attention = []
    for s in changes["steps"]:
        if s["stats"].get("unparsed_examples"):
            ex = ", ".join(f'"{e}"' for e in s["stats"]["unparsed_examples"])
            attention.append(f"Step {s['step']}: {s['stats']['unparsed']} value(s) couldn't be converted and "
                             f"were left as they were, for example {ex}.")
    attention += text.get("notes", []) + notes
    if attention:
        lines += ["", "## Worth a look", ""] + [f"- {a}" for a in attention]
    if plan.get("requirements_coverage"):
        lines += ["", "## Requirements", ""]
        for r in plan["requirements_coverage"]:
            note = f" ({r['note']})" if r.get("note") else ""
            lines.append(f"- {reqs.get(r.get('id'), r.get('id'))}: {str(r.get('status', '')).replace('_', ' ')}{note}")
    lines += ["", "The original file was not changed. Column details are in data-dictionary.csv.", ""]
    return "\n".join(lines)


def write_qa_markdown(job: str, report: dict) -> None:
    """work/qa-report.md: inputs, problems, plan, approvals, checks, data loss, recommendation."""
    profile = common.read_json(common.work(job, "profile.json"))
    intake = common.read_json(common.work(job, "intake.json"))
    plan = common.read_json(common.work(job, "plan.json"))
    approved = approval.is_approved(plan)
    icon = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    lines = [f"# QA report: {common.job_dir(job).name}", "",
             f"**Recommendation: {'PASS' if report['passed'] else 'FAIL'}**"
             + (f" with {len(report['warnings'])} warning(s)" if report["passed"] and report["warnings"] else ""), "",
             "## Input file inspected",
             f"- {profile.get('file')}: {profile.get('rows')} rows x {profile.get('columns')} columns"
             + (f", sheets {profile['sheets']}" if profile.get("sheets") else ""), "",
             "## Problems found at intake",
             f"- {profile.get('duplicate_rows', 0)} exact duplicate rows, {profile.get('empty_rows', 0)} empty rows",
             f"- {sum(c['untrimmed'] for c in profile.get('columns_detail', []))} cells with extra spaces, "
             f"{sum(c['missing'] for c in profile.get('columns_detail', []))} blank cells"]
    lines += [f"- {r}" for r in intake.get("risks", [])]
    lines += ["", "## Cleaning plan", f"See plan-review.md ({len(plan.get('plan', []))} steps)."]
    lines += ["", "## Approvals required"]
    lines += [f"- step {r['step']}: {r['reason']} ({'approved' if approved else 'NOT approved'})"
              for r in plan.get("needs_approval", [])] or ["- None."]
    lines += ["", "## Validation checks", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {c['check']} | {icon[c['result']]} | {str(c['detail']).replace('|', '/')} |" for c in report["checks"]]
    loss = [c for c in report["checks"] if c["check"] in ("No unexpected data loss", "Rows removed within limit",
                                                          "Original file unchanged") and c["result"] != "pass"]
    lines += ["", "## Unexpected changes or data loss"] + ([f"- {c['detail']}" for c in loss] or ["- None found."])
    common.work(job, "qa-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(job: str) -> dict:
    cfg, P = common.load_agent(CONFIG_PATH)
    log = common.get_log(cfg["agent"]["name"])
    changes_path = common.work(job, "changes.json")
    changes = common.read_json(changes_path)
    if not changes:
        raise SystemExit("Run the cleaner's apply step first.")
    output = common.job_dir(job) / "output"
    shutil.rmtree(output, ignore_errors=True)  # never leave an old package next to a new result

    checks, cleaned = run_checks(job, cfg, changes)
    errors = [f"{c['check']}: {c['detail']}" for c in checks if c["result"] == "fail"]
    warnings = [f"{c['check']}: {c['detail']}" for c in checks if c["result"] == "warn"]
    report = {"job": common.job_dir(job).name, "passed": not errors, "checks": checks,
              "errors": errors, "warnings": warnings, "changes_hash": common.file_hash(changes_path)}
    if errors:
        common.write_json(common.work(job, "qa-report.json"), report)
        write_qa_markdown(job, report)
        log.warning("qa failed errors=%d", len(errors))
        print("QA failed:")
        for e in errors:
            print(f"  - {e}")
        return report

    original = common.load_dataset(common.input_files(job)[0])
    payload = {
        "intake_summary": common.read_json(common.work(job, "intake.json")).get("summary"),
        "rows": [changes["input_rows"], changes["output_rows"]],
        "columns": [len(changes["input_columns"]), len(changes["output_columns"])],
        "steps": [{"why": s["why"], "result": describe(s["stats"])} for s in changes["steps"]],
        "warnings": warnings,
    }
    text = common.ask_model(cfg, P, P["system_prompt"].read_text(encoding="utf-8"),
                            json.dumps(payload, indent=1), "report", common.work(job, "qa.raw.txt"))

    source = common.input_files(job)[0]
    output.mkdir(parents=True)
    out_name = f"{source.stem}-cleaned{Path(changes['output_file']).suffix}"
    shutil.copy2(common.work(job, changes["output_file"]), output / out_name)
    wb_notes = common.read_json(common.work(job, "profile.json")).get("workbook", {}).get("notes", [])
    (output / "cleaning-report.md").write_text(build_report(job, changes, original, cleaned, wb_notes, text),
                                               encoding="utf-8")
    pd.DataFrame([{"column": str(c), "type": column_type(cleaned[c]),
                   "filled": int((~common.blank(cleaned[c])).sum()),
                   "filled_pct": round(100 * (~common.blank(cleaned[c])).mean(), 1) if len(cleaned) else 0,
                   "unique_values": int(cleaned[c].astype(str).nunique()),
                   "example": next((str(v)[:60] for v in cleaned[c] if str(v).strip()), "")}
                  for c in cleaned.columns]).to_csv(output / "data-dictionary.csv", index=False, encoding="utf-8-sig")

    report.update(output_files=sorted(p.name for p in output.iterdir()), summary=text.get("report_summary", ""))
    common.write_json(common.work(job, "qa-report.json"), report)
    write_qa_markdown(job, report)
    log.info("DONE qa warnings=%d", len(warnings))
    print(f"QA passed ({len(warnings)} warning(s)). Output: {output}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", help="job folder, e.g. jobs/demo")
    run(parser.parse_args().job)


if __name__ == "__main__":
    main()
