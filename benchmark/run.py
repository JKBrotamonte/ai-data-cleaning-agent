"""Run the agent on every benchmark job and score it.

    python -m benchmark.generate
    python -m benchmark.run              # about $0.25 in API calls for all 5 jobs

Risky steps are approved automatically (--yes) so the run is unattended; the score shows
whether that trust was deserved. Writes benchmark/results.md and benchmark/results.json.
"""

import json
import shutil
import time
from pathlib import Path

from benchmark.generate import DATASETS, JOBS
from benchmark.score import score
from cleaner_agent import __main__ as runner

HERE = Path(__file__).parent
KINDS = ["whitespace", "missing_marker", "date_format", "phone_format", "number_format", "email_case",
         "category_typo", "duplicate_row", "empty_row"]


def main() -> None:
    results = {}
    for name in DATASETS:
        job = JOBS / name
        if not (job / "truth.csv").exists():
            raise SystemExit("Run python -m benchmark.generate first.")
        for d in ("work", "output"):
            shutil.rmtree(job / d, ignore_errors=True)
        print(f"\n######## {name}")
        start = time.time()
        report = runner.run(str(job), assume_yes=True)
        seconds = round(time.time() - start, 1)
        cleaned = job / "output" / f"{name}-cleaned.csv"
        s = score(job, cleaned) if cleaned.exists() else None
        plans = len(list((job / "work").glob("plan.json")))  # attempts are counted in plan.json
        attempt = json.loads((job / "work" / "plan.json").read_text(encoding="utf-8")).get("attempt", 1) \
            if plans else 0
        results[name] = {"qa_passed": report.get("passed", False), "cost_usd": report.get("cost_usd", 0),
                         "seconds": seconds, "plans": attempt, "score": s}
        if s:
            print(f"SCORE {name}: fixed {s['fixed']}/{s['total']}, broke {s['broke_cells']} cells, "
                  f"lost {s['missing_rows']} rows")

    (HERE / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    write_markdown(results)
    print(f"\nWrote {HERE / 'results.md'}")


def write_markdown(results: dict) -> None:
    scored = {n: r for n, r in results.items() if r["score"]}
    fixed = sum(r["score"]["fixed"] for r in scored.values())
    total = sum(r["score"]["total"] for r in scored.values())
    broke = sum(r["score"]["broke_cells"] for r in scored.values())
    lost = sum(r["score"]["missing_rows"] for r in scored.values())
    checked = sum(r["score"]["clean_cells_checked"] for r in scored.values())
    cost = sum(r["cost_usd"] for r in results.values())
    lines = ["# Benchmark results", "",
             f"**Fixed {fixed} of {total} injected errors ({fixed / total:.0%}) across {len(scored)} datasets. "
             f"Broke {broke} of {checked:,} clean cells and lost {lost} real rows. "
             f"Total model cost ${cost:.2f} (${cost / max(len(results), 1):.3f} per file).**", "",
             "| Dataset | Fixed | Broke (cells) | Lost rows | QA | Plans | Cost | Time |",
             "|---|---|---|---|---|---|---|---|"]
    for n, r in results.items():
        s = r["score"]
        lines.append(f"| {n} | {s['fixed']}/{s['total']} ({s['fixed'] / s['total']:.0%}) | {s['broke_cells']} | "
                     f"{s['missing_rows']} | {'pass' if r['qa_passed'] else 'FAIL'} | {r['plans']} | "
                     f"${r['cost_usd']:.3f} | {r['seconds']}s |" if s else
                     f"| {n} | no output | | | {'pass' if r['qa_passed'] else 'FAIL'} | {r['plans']} | "
                     f"${r['cost_usd']:.3f} | {r['seconds']}s |")
    lines += ["", "## By error type", "", "| Error type | Fixed | Share |", "|---|---|---|"]
    for k in KINDS:
        f = sum(r["score"]["by_kind"].get(k, [0, 0])[0] for r in scored.values())
        t = sum(r["score"]["by_kind"].get(k, [0, 0])[1] for r in scored.values())
        if t:
            lines.append(f"| {k.replace('_', ' ')} | {f}/{t} | {f / t:.0%} |")
    examples = [e for r in scored.values() for e in r["score"]["broke_examples"]][:10]
    if examples:
        lines += ["", "## Examples of broken cells", ""] + [f"- {e}" for e in examples]
    lines += ["", "Data is synthetic and seeded (`python -m benchmark.generate`), so the right value of every cell "
              "is known. Risky steps were approved automatically for the run.", ""]
    (HERE / "results.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
