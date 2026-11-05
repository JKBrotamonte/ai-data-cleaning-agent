"""Score a cleaned file against the clean truth.

Rows are matched by the id column (the first column), so removed duplicates don't shift anything.
- fixed: the injected error's cell now equals the clean value; a duplicate's id appears exactly once;
  no fully empty rows remain.
- broke: a cell that was clean before cleaning no longer matches the truth, or a real row went missing.

Numbers are compared by value (383.30 and 383.3 match). Everything else must match exactly,
because the request names each format (dates YYYY-MM-DD, phones 999-999-9999, lowercase emails).
When a duplicate is left behind, a cell counts as right if any copy of the row has it right; the
leftover duplicate is scored once, as an unfixed duplicate_row.
"""

import csv
from pathlib import Path


def read_csv(path: Path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


def norm(value: str, kind: str) -> str:
    v = (value or "").strip()
    if kind == "number" and v:
        try:
            return f"{float(v):.6f}"
        except ValueError:
            return v
    return value or ""  # exact: stray spaces count


def score(job: Path, cleaned_path: Path) -> dict:
    header, truth_rows = read_csv(job / "truth.csv")
    types = {r[0]: r[1] for r in read_csv(job / "types.csv")[1]}
    _, errors = read_csv(job / "errors.csv")
    out_header, out_rows = read_csv(cleaned_path)

    col = {c: i for i, c in enumerate(out_header)}
    truth = {r[0]: r for r in truth_rows}
    by_id = {}
    for r in out_rows:
        by_id.setdefault(r[0].strip() if r else "", []).append(r)
    empty_left = sum(1 for r in out_rows if not any(v.strip() for v in r))

    injected = {}
    results = {}
    for e_id, column, kind, clean, dirty in errors:
        results.setdefault(kind, [0, 0])
        results[kind][1] += 1
        if kind == "empty_row":
            continue  # scored below, all together
        if kind == "duplicate_row":
            ok = len(by_id.get(e_id, [])) == 1
        else:
            injected[(e_id, column)] = kind
            want = norm(clean, types.get(column, ""))
            ok = column in col and any(norm(r[col[column]], types.get(column, "")) == want
                                       for r in by_id.get(e_id, []))
        results[kind][0] += ok
    if "empty_row" in results:
        results["empty_row"][0] = max(0, results["empty_row"][1] - empty_left)

    broke_cells, missing_rows, broke_examples = 0, 0, []
    for rid, t in truth.items():
        rows = by_id.get(rid)
        if not rows:
            missing_rows += 1
            continue
        for j, c in enumerate(header):
            if (rid, c) in injected or c not in col or j == 0:
                continue
            want = norm(t[j], types.get(c, ""))
            if all(norm(r[col[c]], types.get(c, "")) != want for r in rows):
                broke_cells += 1
                if len(broke_examples) < 5:
                    broke_examples.append(f"{rid}/{c}: '{t[j]}' became '{rows[0][col[c]]}'")
    missing_cols = [c for c in header if c not in col]

    fixed = sum(v[0] for v in results.values())
    total = sum(v[1] for v in results.values())
    return {"fixed": fixed, "total": total, "by_kind": results, "broke_cells": broke_cells,
            "missing_rows": missing_rows, "missing_columns": missing_cols, "broke_examples": broke_examples,
            "clean_cells_checked": sum(len(header) - 1 for _ in truth) - len(injected)}
