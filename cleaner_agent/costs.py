"""Cost log: every model call is one row in costs.csv, with a monthly cap.

Columns: date, step, label, input_tokens, output_tokens, cost_usd
"""

import csv
from datetime import date
from pathlib import Path

HEADER = ["date", "step", "label", "input_tokens", "output_tokens", "cost_usd"]


def append(path: Path, step: str, label: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow([date.today().isoformat(), step, label, input_tokens, output_tokens, f"{cost_usd:.4f}"])


def month_spend(path: Path, step: str = None) -> float:
    """Cost this calendar month, for one step or (step=None) for all of them."""
    path = Path(path)
    if not path.exists():
        return 0.0
    prefix = date.today().strftime("%Y-%m")
    with path.open(encoding="utf-8") as f:
        return sum(float(r["cost_usd"]) for r in csv.DictReader(f)
                   if r["date"].startswith(prefix) and (step is None or r["step"] == step))


def check_budget(path: Path, step: str, cap_usd: float) -> None:
    """Stop before a model call once this step's spend this month reaches its cap."""
    spent = month_spend(path, step)
    if spent >= cap_usd:
        raise SystemExit(f"{step}: monthly cap reached (${spent:.4f} of ${cap_usd:.2f}). "
                         "Raise budget.monthly_cap_usd in its config.yaml to continue.")
    if spent >= 0.8 * cap_usd:
        print(f"Warning: {step} has used ${spent:.4f} of its ${cap_usd:.2f} monthly cap.")
