"""Helpers the pipeline steps share: job folders, reading and writing datasets,
profiling, privacy masking and freshness hashes.

Privacy rule: the model never sees your file. It sees column statistics,
value "shapes" (letters -> a/A, digits -> 9), and a small sample in which
personal columns are masked the same way. All changes run here, in code.
"""

import hashlib
import json
import random
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

from cleaner_agent.llm import ask_model, extract_json  # noqa: F401 (steps call common.ask_model)

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / "jobs"
DATA_EXTS = {".csv", ".tsv", ".xlsx", ".json"}
EXCEL_MAX_ROWS = 1_048_575  # plus the header row

PII_HEADER = re.compile(r"name|e-?mail|phone|mobile|cell|tel\b|address|street|city|zip|postal|"
                        r"ssn|social|birth|dob|passport|licen[cs]e|account|card|iban|ip_?addr", re.I)
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PHONE = re.compile(r"^\+?[\d\s().-]{7,}$")


# ---------- config, jobs ----------

def from_root(path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def load_agent(config_path: Path):
    """A step's config.yaml, with every entry under paths: resolved against the repo root."""
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    return cfg, {k: from_root(v) for k, v in cfg.get("paths", {}).items()}


def job_dir(job: str) -> Path:
    """A job is a folder name under jobs/ (e.g. 'demo') or a path to one (e.g. 'jobs/demo')."""
    p = Path(job)
    return p if p.is_absolute() or p.parent != Path(".") else JOBS_DIR / job


def work(job: str, name: str) -> Path:
    path = job_dir(job) / "work" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_request(job: str) -> dict:
    """Parse request.md: 'key: value' lines above the first '## ' heading, then sections.
    Template placeholders in (parentheses) count as empty."""
    path = job_dir(job) / "request.md"
    if not path.exists():
        sys.exit(f"No request.md in {path.parent}. Copy jobs/demo/request.md and edit it.")
    fields, sections, current = {}, {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
        elif current is None:
            m = re.match(r"^([a-z_]+):[ \t]*([^#]*?)[ \t]*(#.*)?$", line)
            if m:
                fields[m.group(1)] = m.group(2)
        else:
            sections[current].append(line)
    clean = {}
    for key, lines in sections.items():
        text = "\n".join(lines).strip()
        clean[key] = "" if re.fullmatch(r"\([^)]*\)", text, re.S) else text
    return {"fields": fields, "sections": clean}


def yes(value) -> bool:
    return str(value or "").strip().lower() in ("yes", "y", "true")


def input_files(job: str) -> list:
    folder = job_dir(job) / "input"
    return sorted(p for p in folder.glob("*") if p.is_file() and p.suffix.lower() in DATA_EXTS) \
        if folder.exists() else []


def other_files(job: str) -> list:
    folder = job_dir(job) / "input"
    return sorted(p.name for p in folder.glob("*")
                  if p.is_file() and p.suffix.lower() not in DATA_EXTS) if folder.exists() else []


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------- freshness ----------

def fingerprint(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else ""


def intake_hash(job: str) -> str:
    request = read_request(job)
    s = request["sections"]
    return fingerprint(s.get("requirements", ""), s.get("answers", ""),
                       [file_hash(p) for p in input_files(job)])


def plan_hash(job: str) -> str:
    """Changes when the intake result or the revision requests change."""
    return fingerprint(file_hash(work(job, "intake.json")),
                       read_request(job)["sections"].get("revision requests", ""))


def intake_blockers(job: str) -> list:
    """Why the job can't go to cleaning yet. 'proceed: yes' in request.md overrides."""
    intake = read_json(work(job, "intake.json"))
    if yes(read_request(job)["fields"].get("proceed")):
        return []
    blockers = ["the requirements seem to ask for no AI tools; clean this file by hand"] \
        if intake.get("requested_no_ai") else []
    blockers += [f"question: {q}" for q in intake.get("questions", [])]
    blockers += [f"scope: {p}" for p in intake.get("scope_issues", [])]
    workbook = read_json(work(job, "profile.json")).get("workbook", {})
    blockers += [f"workbook (confirm this is fine): {w}" for w in workbook.get("confirm", [])]
    return blockers


# ---------- datasets ----------

def load_dataset(path: Path) -> pd.DataFrame:
    """Read every cell as text so nothing changes on the way in. Empty string = missing."""
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv"):
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return pd.read_csv(path, sep="\t" if ext == ".tsv" else None, engine="python",
                                   dtype=str, keep_default_na=False, encoding=enc)
            except UnicodeDecodeError:
                continue
    if ext == ".xlsx":
        return pd.read_excel(path, dtype=str, keep_default_na=False).fillna("")
    if ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            if len(lists) != 1:
                raise ValueError("JSON must be a list of records, or an object holding exactly one list.")
            data = lists[0]
        df = pd.json_normalize(data)
        return df.astype(object).where(df.notna(), "").astype(str)
    raise ValueError(f"Unsupported file type: {path.name}")


def sheet_names(path: Path) -> list:
    if path.suffix.lower() != ".xlsx":
        return []
    return pd.ExcelFile(path).sheet_names


def write_dataset(df: pd.DataFrame, path: Path) -> None:
    if path.resolve().parent.name == "input":  # never overwrite the original files
        raise PermissionError(f"Refusing to write into a job's input/ folder: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    if ext == ".csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    elif ext == ".tsv":
        df.to_csv(path, sep="\t", index=False, encoding="utf-8-sig")
    elif ext == ".xlsx":
        if len(df) > EXCEL_MAX_ROWS:
            raise ValueError(f"{len(df)} rows is over Excel's limit; deliver as CSV.")
        df.to_excel(path, index=False)
    elif ext == ".json":
        df.to_json(path, orient="records", indent=2, force_ascii=False)
    else:
        raise ValueError(f"Unsupported output type: {path.suffix}")


def blank(series: pd.Series) -> pd.Series:
    return series.map(lambda v: v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "")


# ---------- profiling and masking ----------

def shape(value, max_len: int = 40) -> str:
    """'John Smith' -> 'Aa Aa', '(555) 123-4567' -> '(999) 999-9999'. Keeps format, drops content."""
    out = []
    for ch in str(value):
        c = "9" if ch.isdigit() else "A" if ch.isupper() else "a" if ch.isalpha() else ch
        if c in "Aa" and out and out[-1] == c:
            continue
        out.append(c)
    s = "".join(out)
    return s if len(s) <= max_len else s[:max_len] + "…"


def is_pii_column(name, series: pd.Series) -> bool:
    if PII_HEADER.search(str(name)):
        return True
    sample = series[~blank(series)].astype(str).head(300)
    if sample.empty:
        return False
    return sample.str.contains(EMAIL).mean() > 0.2 or sample.str.match(PHONE).mean() > 0.2


SHARING_MODES = ("pii_masked", "all_masked", "none")


DEFAULT_SHARING = "all_masked"  # the safest mode the pipeline can still run with


def ai_sharing(job: str) -> str:
    """What may reach the model, set by ai_data_sharing in request.md.
    pii_masked: stats + samples with personal columns masked; all_masked (default): stats and
    patterns only; none: nothing, so the job can't use the model."""
    mode = read_request(job)["fields"].get("ai_data_sharing") or DEFAULT_SHARING
    if mode not in SHARING_MODES:
        sys.exit(f"ai_data_sharing must be one of {SHARING_MODES} (got '{mode}'). Set it in request.md.")
    return mode


def masked_columns(df: pd.DataFrame, mode: str) -> set:
    if mode == "pii_masked":
        return {c for c in df.columns if is_pii_column(c, df[c])}
    return set(df.columns)


def workbook_features(path: Path) -> dict:
    """Excel features the cleaned file can't keep. pandas reads values only, so these are flagged, never
    silently dropped. 'confirm' items stop the job until you agree; 'notes' go in the report.
    Formulas aren't evaluated and macros never run: openpyxl only reads the file."""
    if path.suffix.lower() != ".xlsx":
        return {"confirm": [], "notes": []}
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=False)
    cached = openpyxl.load_workbook(path, data_only=True)  # the values Excel last saved for each formula
    confirm, notes = [], []
    if len(wb.sheetnames) > 1:
        confirm.append(f"{len(wb.sheetnames)} sheets; only the first ('{wb.sheetnames[0]}') is cleaned and delivered.")
    for ws in wb.worksheets:
        name = f"Sheet '{ws.title}'"
        if ws.sheet_state != "visible":
            confirm.append(f"{name} is hidden.")
        cells = [cell.coordinate for row in ws.iter_rows() for cell in row if cell.data_type == "f"]
        if cells:
            confirm.append(f"{name} has {len(cells)} formula(s); the cleaned file holds their last saved values, not the formulas.")
            unsaved = sum(1 for c in cells if cached[ws.title][c].value is None)
            if unsaved:
                confirm.append(f"{name}: {unsaved} formula(s) have no saved value (the file wasn't last saved in Excel), "
                               "so they will come out blank. Open and re-save it in Excel first.")
        if ws.merged_cells.ranges:
            confirm.append(f"{name} has {len(ws.merged_cells.ranges)} merged cell range(s); only the top-left cell keeps the value.")
        if ws.conditional_formatting:
            notes.append(f"{name}: conditional formatting is not kept.")
        if ws.data_validations.dataValidation:
            notes.append(f"{name}: dropdowns and data validation rules are not kept.")
        if ws.tables:
            notes.append(f"{name}: Excel table formatting ({len(ws.tables)}) is not kept.")
    if wb.defined_names:
        notes.append("Named ranges are not kept.")
    notes.append("Cell formatting (fonts, colours, column widths) is not kept.")
    return {"confirm": confirm, "notes": notes}


def similar_values(filled: pd.Series, max_unique: int = 500, cutoff: int = 90, limit: int = 5) -> list:
    """Groups of values that are probably the same thing written differently ("New York", "new york",
    "New  York City" vs "New York city"), found with RapidFuzz. Only for text columns with at most
    max_unique distinct values; mostly-numeric values (IDs, amounts) are skipped. Each group lists
    {value: count}, most common first. The cleaner can fix a group with replace_values, which needs
    your approval."""
    from rapidfuzz import fuzz, process, utils
    counts = filled.value_counts()
    if not 2 <= len(counts) <= max_unique:
        return []
    vals = [v for v in counts.index if sum(ch.isdigit() for ch in v) < len(v) / 2]
    if len(vals) < 2:
        return []
    scores = process.cdist(vals, vals, scorer=fuzz.token_sort_ratio, processor=utils.default_process,
                           score_cutoff=cutoff)
    parent = list(range(len(vals)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in zip(*scores.nonzero()):
        if i < j:
            parent[root(j)] = root(i)
    groups = {}
    for i, v in enumerate(vals):
        groups.setdefault(root(i), []).append(v)
    out = [g for g in groups.values() if len(g) > 1]
    out.sort(key=lambda g: -sum(int(counts[v]) for v in g))
    return [{v[:60]: int(counts[v]) for v in sorted(g, key=lambda v: -counts[v])[:6]} for g in out[:limit]]


def profile(df: pd.DataFrame, masked: set) -> dict:
    cols = []
    for c in df.columns:
        s = df[c].astype(str)
        filled = s[~blank(s)]
        info = {"name": str(c), "masked": c in masked, "missing": int(len(s) - len(filled)),
                "unique": int(filled.nunique()),
                "patterns": {k: int(v) for k, v in filled.map(shape).value_counts().head(8).items()},
                "untrimmed": int((filled != filled.str.strip()).sum())}
        if c not in masked:
            if info["unique"] <= 25:
                info["values"] = {k[:60]: int(v) for k, v in filled.value_counts().head(25).items()}
            else:
                info["examples"] = [v[:60] for v in filled.drop_duplicates().head(5)]
            groups = similar_values(filled)
            if groups:
                info["similar_values"] = groups
        cols.append(info)
    empty_rows = int(df.apply(blank).all(axis=1).sum()) if len(df.columns) else 0
    return {"rows": len(df), "columns": len(df.columns),
            "duplicate_rows": int(df.duplicated().sum()), "empty_rows": empty_rows,
            "columns_detail": cols}


def sample_rows(df: pd.DataFrame, masked: set, n: int = 20) -> list:
    """The first rows (up to 8) plus a fixed-seed random pick of the rest, n rows in all."""
    head = list(range(min(8, n, len(df))))
    rest = range(len(head), len(df))
    picks = head + sorted(random.Random(0).sample(rest, max(0, min(n - len(head), len(rest)))))
    return [{str(c): (shape(v) if c in masked else str(v)[:80]) for c, v in df.iloc[i].items()}
            for i in picks]


# ---------- logging ----------

def get_log(name: str):
    """A logger writing to logs/<name>.log (counts and file names only, never data values)."""
    import logging
    log = logging.getLogger(name)
    if not log.handlers:
        (ROOT / "logs").mkdir(exist_ok=True)
        h = logging.FileHandler(ROOT / "logs" / f"{name}.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log
