"""Intake: profile the file and list anything to settle before cleaning.

Code profiles the file and checks the row and column limits. The model reads the
profile (never the file) and the request, and returns concrete requirements,
questions, and risks.

    python -m cleaner_agent.intake.agent jobs/demo

Writes jobs/<name>/work/profile.json and intake.json.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from cleaner_agent import common

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def run(job: str) -> dict:
    cfg, P = common.load_agent(CONFIG_PATH)
    log = common.get_log(cfg["agent"]["name"])
    request = common.read_request(job)
    files = common.input_files(job)
    if len(files) != 1:
        raise SystemExit(f"Intake needs exactly one data file in {common.job_dir(job) / 'input'} (found {len(files)}).")

    sharing = common.ai_sharing(job)
    if sharing == "none":
        raise SystemExit("ai_data_sharing is 'none' for this job, so no data may go to the model. Clean it by hand.")
    df = common.load_dataset(files[0])
    masked = common.masked_columns(df, sharing)
    profile = common.profile(df, masked)
    profile["file"] = files[0].name
    profile["sheets"] = common.sheet_names(files[0])
    profile["workbook"] = common.workbook_features(files[0])
    common.write_json(common.work(job, "profile.json"), profile)

    # Limits are checked in code, before any model call.
    scope_issues = []
    if profile["rows"] > cfg["max_rows"]:
        scope_issues.append(f"{profile['rows']} rows is over the limit of {cfg['max_rows']}.")
    if profile["columns"] > cfg["max_columns"]:
        scope_issues.append(f"{profile['columns']} columns is over the limit of {cfg['max_columns']}.")
    if common.other_files(job):
        scope_issues.append(f"Non-data files in input/ were not read: {', '.join(common.other_files(job))}.")
    if any("over the limit" in s for s in scope_issues):
        raise SystemExit("; ".join(scope_issues))

    payload = {
        "today": str(date.today()),
        "requirements": request["sections"].get("requirements", ""),
        "answers": request["sections"].get("answers", ""),
        "profile": profile,
        "sample_rows": common.sample_rows(df, masked, cfg["sample_rows"]),
    }
    result = common.ask_model(cfg, P, P["system_prompt"].read_text(encoding="utf-8"),
                              json.dumps(payload, indent=1, default=str), "intake",
                              common.work(job, "intake.raw.txt"))

    result["questions"] = [q for q in result.get("questions", []) if q][:cfg["max_questions"]]
    result["scope_issues"] = scope_issues
    result["inputs_hash"] = common.intake_hash(job)
    common.write_json(common.work(job, "intake.json"), result)

    log.info("DONE intake rows=%d masked=%d questions=%d", profile["rows"], len(masked), len(result["questions"]))
    print(f"Intake: {profile['rows']} rows x {profile['columns']} columns, "
          f"{len(masked)} column(s) masked from the model.")
    for q in result["questions"]:
        print(f"  Question: {q}")
    for s in scope_issues:
        print(f"  Scope: {s}")
    for w in profile["workbook"]["confirm"]:
        print(f"  Workbook: {w}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", help="job folder, e.g. jobs/demo")
    run(parser.parse_args().job)


if __name__ == "__main__":
    main()
