"""The cleaning operations the model may put in a plan.

The model only picks operations and arguments. This code makes every change and
counts it, so the change log in the delivery report is exact. No model-written
code ever runs. A value that can't be converted is left as it was and counted
as "unparsed", never silently blanked.
"""

import re
from datetime import datetime

import dateparser
import pandas as pd

from cleaner_agent.common import blank


class PlanError(ValueError):
    pass


def _diff(before: pd.Series, after: pd.Series) -> int:
    return int((before.astype(str) != after.astype(str)).sum())


def _cols(df, step, key="columns", default_all=False):
    cols = step.get(key)
    if cols in (None, "all", "*", []):
        if default_all:
            return list(df.columns)
        raise PlanError(f"'{key}' is required")
    if isinstance(cols, str):
        cols = [cols]
    unknown = [c for c in cols if c not in df.columns]
    if unknown:
        raise PlanError(f"unknown column(s) {unknown}; columns are {list(df.columns)}")
    return cols


def _map_text(df, cols, fn):
    """Apply fn to non-blank text cells; returns cells changed."""
    changed = 0
    for c in cols:
        before = df[c]
        after = before.map(lambda v: fn(v) if isinstance(v, str) and v.strip() else v)
        changed += _diff(before, after)
        df[c] = after
    return changed


def _examples(values, limit=5):
    return [str(v)[:60] for v in list(dict.fromkeys(values))[:limit]]


# ---------- operations: each takes (df, step) and returns (df, stats) ----------

def trim_whitespace(df, step):
    inner = step.get("collapse_inner_spaces", True)
    fn = (lambda v: re.sub(r"\s+", " ", v).strip()) if inner else (lambda v: v.strip())
    cols = _cols(df, step, default_all=True)
    changed = 0
    for c in cols:  # also trims whitespace-only cells to empty
        before = df[c]
        df[c] = before.map(lambda v: fn(v) if isinstance(v, str) else v)
        changed += _diff(before, df[c])
    return df, {"cells_changed": changed}


def standardize_missing(df, step):
    tokens = {t.strip().lower() for t in step.get("tokens", ["n/a", "na", "null", "none", "-", "--", "?", "nan"])}
    cols = _cols(df, step, default_all=True)
    changed = 0
    for c in cols:
        before = df[c]
        df[c] = before.map(lambda v: "" if isinstance(v, str) and v.strip().lower() in tokens else v)
        changed += _diff(before, df[c])
    return df, {"cells_changed": changed}


def _title(v: str) -> str:
    return re.sub(r"[a-z][a-z']*", lambda m: m.group(0)[0].upper() + m.group(0)[1:], v.lower())


def normalize_case(df, step):
    case = step.get("case")
    fns = {"lower": str.lower, "upper": str.upper, "title": _title,
           "sentence": lambda v: v[:1].upper() + v[1:].lower()}
    if case not in fns:
        raise PlanError(f"case must be one of {list(fns)}")
    return df, {"cells_changed": _map_text(df, _cols(df, step), fns[case])}


def rename_columns(df, step):
    mapping = step.get("mapping") or {}
    _cols(df, {"columns": list(mapping)})
    new_names = [mapping.get(c, c) for c in df.columns]
    if len(set(new_names)) != len(new_names):
        raise PlanError("renaming would create duplicate column names")
    return df.rename(columns=mapping), {"columns_renamed": len(mapping)}


def drop_columns(df, step):
    cols = _cols(df, step)
    return df.drop(columns=cols), {"columns_removed": len(cols)}


def reorder_columns(df, step):
    order = _cols(df, step, key="order")
    return df[order + [c for c in df.columns if c not in order]], {}


def drop_duplicates(df, step):
    subset = _cols(df, step, key="subset", default_all=True)
    keep = step.get("keep", "first")
    if keep not in ("first", "last"):
        raise PlanError("keep must be 'first' or 'last'")
    key = df[subset].astype(str)
    if step.get("ignore_case_and_spaces", False):
        key = key.apply(lambda s: s.str.strip().str.lower())
    dupes = key.duplicated(keep=keep)
    return df[~dupes].reset_index(drop=True), {"rows_removed": int(dupes.sum())}


def drop_empty_rows(df, step):
    empty = df.apply(blank).all(axis=1)
    return df[~empty].reset_index(drop=True), {"rows_removed": int(empty.sum())}


def drop_empty_columns(df, step):
    empty = [c for c in df.columns if blank(df[c]).all()]
    return df.drop(columns=empty), {"columns_removed": len(empty), "columns": empty}


def filter_rows(df, step):
    c = _cols(df, step, key="column")[0]
    cond, value, action = step.get("condition"), str(step.get("value", "")), step.get("action", "drop")
    s = df[c].astype(str)
    tests = {
        "equals": lambda: s.str.strip().str.lower() == value.strip().lower(),
        "not_equals": lambda: s.str.strip().str.lower() != value.strip().lower(),
        "contains": lambda: s.str.contains(value, case=False, regex=False),
        "matches": lambda: s.str.fullmatch(_regex(value)),
        "empty": lambda: blank(df[c]),
        "not_empty": lambda: ~blank(df[c]),
    }
    if cond not in tests or action not in ("drop", "keep"):
        raise PlanError(f"condition must be one of {list(tests)} and action 'drop' or 'keep'")
    hit = tests[cond]().fillna(False).astype(bool)
    remove = hit if action == "drop" else ~hit
    return df[~remove].reset_index(drop=True), {"rows_removed": int(remove.sum())}


def replace_values(df, step):
    mapping = {str(k): v for k, v in (step.get("mapping") or {}).items()}
    if not mapping:
        raise PlanError("mapping is required")
    if step.get("ignore_case", True):
        lookup = {k.strip().lower(): v for k, v in mapping.items()}
        fn = lambda v: lookup.get(v.strip().lower(), v)
    else:
        fn = lambda v: mapping.get(v, v)
    return df, {"cells_changed": _map_text(df, _cols(df, step), fn)}


def _regex(pattern: str):
    if not pattern or len(pattern) > 200:
        raise PlanError("pattern must be 1-200 characters")
    try:
        return re.compile(pattern)
    except re.error as e:
        raise PlanError(f"bad regex: {e}")


def regex_replace(df, step):
    rx = _regex(step.get("pattern", ""))
    repl = str(step.get("replacement", ""))
    return df, {"cells_changed": _map_text(df, _cols(df, step), lambda v: rx.sub(repl, v))}


def _convert(df, cols, convert):
    """Convert each unique non-blank text value once; failures stay as they were."""
    changed, unparsed, examples = 0, 0, []
    for c in cols:
        before = df[c]
        cache, failed = {}, []
        for v in before.unique():
            if isinstance(v, str) and v.strip():
                out = convert(v.strip())
                if out is None:
                    failed.append(v)
                cache[v] = v if out is None else out
        df[c] = before.map(lambda v: cache.get(v, v) if isinstance(v, str) else v)
        changed += _diff(before, df[c])
        bad = before.isin(failed)
        unparsed += int(bad.sum())
        examples += list(before[bad].head(5))
    return {"cells_changed": changed, "unparsed": unparsed, "unparsed_examples": _examples(examples)}


def standardize_dates(df, step):
    fmt = step.get("output_format", "%Y-%m-%d")
    formats = step.get("input_formats") or []
    dayfirst = bool(step.get("dayfirst", False))
    try:
        datetime(2024, 1, 31).strftime(fmt)
    except ValueError:
        raise PlanError(f"bad output_format {fmt}")

    def convert(v):
        for f in formats:
            try:
                return datetime.strptime(v, f).strftime(fmt)
            except ValueError:
                pass
        if len(v) < 6 or not re.search(r"\d", v):
            return None
        try:
            ts = pd.to_datetime(v, dayfirst=dayfirst, errors="coerce")
        except (ValueError, OverflowError):
            ts = pd.NaT
        if pd.isna(ts):  # "March 3rd, 2024", "3 mars 2024": dateparser, full dates only, never relative ones
            ts = dateparser.parse(v, settings={
                "DATE_ORDER": "DMY" if dayfirst else "MDY", "STRICT_PARSING": True,
                "REQUIRE_PARTS": ["day", "month", "year"], "PARSERS": ["absolute-time"],
                "RETURN_AS_TIMEZONE_AWARE": False})
        return None if ts is None or pd.isna(ts) else ts.strftime(fmt)

    return df, _convert(df, _cols(df, step), convert)


def to_number(df, step):
    decimal = step.get("decimal", ".")
    as_fraction = step.get("percent_as_fraction", False)

    def convert(v):
        s, neg = v, False
        if s.startswith("(") and s.endswith(")"):
            s, neg = s[1:-1], True
        pct = s.endswith("%")
        s = re.sub(r"[\s$€£¥%]|USD|EUR|GBP", "", s.rstrip("%"))
        s = s.replace("." if decimal == "," else ",", "")
        if decimal == ",":
            s = s.replace(",", ".")
        try:
            n = float(s)
        except ValueError:
            return None
        n = -n if neg else n
        n = n / 100 if pct and as_fraction else n
        return int(n) if n.is_integer() and "." not in s else round(n, 10)

    return df, _convert(df, _cols(df, step), convert)


def standardize_phone(df, step):
    """Google's libphonenumber (the phonenumbers package): any country, the country's own layout.
    A number that can't exist (wrong length) is left as it was. One that could exist but isn't
    assigned (like 555 numbers) is still formatted, and counted as not_valid for the report."""
    import phonenumbers as pn
    fmt = step.get("format", "national")
    country = str(step.get("country", "US")).upper()
    if fmt not in ("national", "e164", "digits"):
        raise PlanError("format must be national, e164, or digits")
    if country not in pn.SUPPORTED_REGIONS:
        raise PlanError(f"unknown country '{country}'; use a two-letter code like US, GB, or IN")
    not_valid = set()

    def convert(v):
        if re.search(r"[a-wyzA-WYZ]", v) and not re.search(r"\bext\.?\s*\d+$", v, re.I):
            return None  # words, apart from an extension (x123, ext. 123)
        try:
            n = pn.parse(v, country)
        except pn.NumberParseException:
            return None
        if not pn.is_possible_number(n):
            return None
        if not pn.is_valid_number(n):
            not_valid.add(v)
        home = pn.region_code_for_number(n) in (country, None)
        if fmt == "e164":
            return pn.format_number(n, pn.PhoneNumberFormat.E164)
        if fmt == "digits":
            return str(n.national_number) if home else pn.format_number(n, pn.PhoneNumberFormat.E164)[1:]
        return pn.format_number(n, pn.PhoneNumberFormat.NATIONAL if home else pn.PhoneNumberFormat.INTERNATIONAL)

    cols = _cols(df, step)
    before = {c: df[c].map(lambda v: v.strip() if isinstance(v, str) else v) for c in cols}
    stats = _convert(df, cols, convert)
    stats["not_valid"] = int(sum(before[c].isin(not_valid).sum() for c in cols))
    return df, stats


def standardize_email(df, step):
    """python-email-validator checks the syntax (no network lookups); valid addresses are lowercased."""
    from email_validator import EmailNotValidError, validate_email

    def convert(v):
        try:
            return validate_email(v, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            return None

    return df, _convert(df, _cols(df, step), convert)


def split_column(df, step):
    c = _cols(df, step, key="column")[0]
    into, sep = step.get("into") or [], step.get("separator")
    if len(into) < 2 or not sep:
        raise PlanError("'into' needs 2+ new column names and 'separator' is required")
    clash = [n for n in into if n in df.columns and n != c]
    if clash:
        raise PlanError(f"new column names already exist: {clash}")
    parts = df[c].astype(str).str.split(sep, n=len(into) - 1, regex=False)
    short = int((parts.map(len) < len(into)).sum() - blank(df[c]).sum())
    pos = list(df.columns).index(c)
    new = pd.DataFrame({n: parts.map(lambda p, i=i: p[i].strip() if i < len(p) else "") for i, n in enumerate(into)})
    if not step.get("keep_original", False):
        df = df.drop(columns=[c])
    else:
        pos += 1
    for i, n in enumerate(into):
        df.insert(pos + i, n, new[n].values)
    return df, {"columns_added": len(into), "rows_with_fewer_parts": max(short, 0)}


def merge_columns(df, step):
    cols, into = _cols(df, step), step.get("into")
    if not into or (into in df.columns and into not in cols):
        raise PlanError("'into' must be a new column name (or one of the merged columns)")
    sep = step.get("separator", " ")
    merged = df[cols].apply(lambda r: sep.join(str(v).strip() for v in r if str(v).strip()), axis=1)
    pos = list(df.columns).index(cols[0])
    if not step.get("keep_originals", False):
        df = df.drop(columns=cols)
        df.insert(min(pos, len(df.columns)), into, merged.values)
    elif into in df.columns:
        df[into] = merged.values
    else:
        df.insert(pos, into, merged.values)
    return df, {"columns_merged": len(cols)}


def fill_missing(df, step):
    strategy = step.get("strategy")
    total = 0
    for c in _cols(df, step):
        empty = blank(df[c])
        if strategy == "value":
            if "value" not in step:
                raise PlanError("strategy 'value' needs 'value'")
            df.loc[empty, c] = step["value"]
        elif strategy in ("mean", "median"):
            nums = pd.to_numeric(df.loc[~empty, c], errors="coerce")
            if nums.isna().any() or nums.empty:
                raise PlanError(f"{strategy} needs an all-numeric column; run to_number on '{c}' first")
            df.loc[empty, c] = round(float(getattr(nums, strategy)()), 6)
        elif strategy == "mode":
            mode = df.loc[~empty, c].astype(str).mode()
            if mode.empty:
                continue
            df.loc[empty, c] = mode.iloc[0]
        elif strategy in ("forward", "backward"):
            s = df[c].where(~empty)
            s = s.ffill() if strategy == "forward" else s.bfill()
            df[c] = s.where(s.notna(), "")
        else:
            raise PlanError("strategy must be value, mean, median, mode, forward, or backward")
        total += int(empty.sum() - blank(df[c]).sum())
    return df, {"cells_filled": total}


def sort_rows(df, step):
    cols = _cols(df, step)
    return df.sort_values(cols, ascending=step.get("ascending", True), kind="stable",
                          key=lambda s: s.astype(str).str.lower()).reset_index(drop=True), {}


# Can the cleaned file be turned back without the original? (The original is always kept in input/.)
REVERSIBLE = {"rename_columns", "reorder_columns"}
# Steps whose job is to blank cells: anything else that blanks a cell is unexpected data loss.
BLANKING_OPS = {"standardize_missing", "trim_whitespace"}

OPS = {f.__name__: f for f in (
    trim_whitespace, standardize_missing, normalize_case, rename_columns, drop_columns, reorder_columns,
    drop_duplicates, drop_empty_rows, drop_empty_columns, filter_rows, replace_values, regex_replace,
    standardize_dates, to_number, standardize_phone, standardize_email, split_column, merge_columns,
    fill_missing, sort_rows)}


def run_plan(df: pd.DataFrame, plan: list, max_steps: int):
    """Run every step in order. Returns (df, change log). Raises PlanError naming the bad step."""
    if not isinstance(plan, list) or not plan:
        raise PlanError("plan must be a non-empty list of steps")
    if len(plan) > max_steps:
        raise PlanError(f"plan has {len(plan)} steps; the limit is {max_steps}")
    changes = []
    for i, step in enumerate(plan, 1):
        op = step.get("op") if isinstance(step, dict) else None
        if op not in OPS:
            raise PlanError(f"step {i}: unknown op '{op}'")
        if not str(step.get("why", "")).strip():
            raise PlanError(f"step {i} ({op}): 'why' is required")
        before = df
        try:
            df, stats = OPS[op](df.copy(), step)
        except PlanError as e:
            raise PlanError(f"step {i} ({op}): {e}")
        stats.setdefault("rows_removed", len(before) - len(df))
        if len(df) == len(before):  # cells that held a value before this step and are blank after it
            common_cols = [c for c in before.columns if c in df.columns]
            stats["cells_blanked"] = int(sum(((~blank(before[c])) & blank(df[c])).sum() for c in common_cols))
        changes.append({"step": i, "op": op, "why": step["why"],
                        "args": {k: v for k, v in step.items() if k not in ("op", "why")}, "stats": stats})
    return df, changes
