#!/usr/bin/env python3
import argparse
import csv
import gzip
import os
import re
import shutil
from glob import glob
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Accept multi-letter group codes, e.g. D01, DK03, HC12, K7
SID_RE = re.compile(r"^([A-Za-z]+)(\d+)$")

def split_sid(sid: str) -> Tuple[str, str]:
    m = SID_RE.match(sid)
    if not m:
        raise ValueError(f"Subject ID '{sid}' must look like <Letters><Digits> (e.g., D01, DK03)")
    return m.group(1), m.group(2)  # (group, number)

def find_subject_dirs(base: Path) -> List[Path]:
    """Return sorted <root>/<T1_CAT12(_Kontrollen)>/<SubjectID>/ directories that look like IDs."""
    if not base.exists():
        return []
    subs = [p for p in base.iterdir() if p.is_dir() and SID_RE.match(p.name)]
    subs.sort(key=lambda p: p.name)  # Lexicographic order (what SPM uses)
    return subs

def copy_seg_for_sid(subject_dir: Path, out_root: Path, release: str) -> Tuple[str, str, Path, Path]:
    """
    Copy mwp1/mwp2 from <subject>/mri into:
      out_root/<Group>/rp1<release>/rp1_<SID>_T1.nii
      out_root/<Group>/rp2<release>/rp2_<SID>_T1.nii
    """
    sid = subject_dir.name
    group, _ = split_sid(sid)
    mri_dir = subject_dir / "mri"
    if not mri_dir.is_dir():
        raise FileNotFoundError(f"Missing mri/ for {sid}: {mri_dir}")

    def find_one(prefix: str) -> Path:
        # Prefer uncompressed .nii if both exist
        cands = sorted(glob(str(mri_dir / f"{prefix}{sid}_T1.nii"))) + \
                sorted(glob(str(mri_dir / f"{prefix}{sid}_T1.nii.gz")))
        if not cands:
            raise FileNotFoundError(f"Missing {prefix}{sid}_T1.nii[.gz] in {mri_dir}")
        for c in cands:
            if c.endswith(".nii"):
                return Path(c)
        return Path(cands[0])

    src_gm = find_one("mwp1")  # GM
    src_wm = find_one("mwp2")  # WM

    rp1_dir = out_root / group / f"rp1{release}"
    rp2_dir = out_root / group / f"rp2{release}"
    rp1_dir.mkdir(parents=True, exist_ok=True)
    rp2_dir.mkdir(parents=True, exist_ok=True)

    dst_gm = rp1_dir / f"rp1_{sid}_T1.nii"
    dst_wm = rp2_dir / f"rp2_{sid}_T1.nii"

    def copy_as_nii(src: Path, dst: Path):
        if src.suffix == ".gz":
            with gzip.open(src, "rb") as f_in, open(dst, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copy2(src, dst)

    copy_as_nii(src_gm, dst_gm)
    copy_as_nii(src_wm, dst_wm)
    return group, sid, dst_gm, dst_wm

# ---------- CSV handling ----------

def _sniff_delimiter(path: Path) -> str:
    sample = path.read_bytes()[:65536].decode("utf-8", errors="ignore")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t,")
        return dialect.delimiter
    except Exception:
        # Fallback to comma
        return ","

def parse_csv(
    csv_path: Path,
    id_col: Optional[int],
    age_col: Optional[int],
    sex_col: Optional[int],
    id_name: Optional[str],
    age_name: Optional[str],
    sex_name: Optional[str],
) -> Dict[str, Tuple[int, int]]:
    """
    Return mapping SID -> (age_int, sex_int[0/1]).
    - If *_name provided, use header names (case-insensitive).
    - Else use 1-based column indices (id_col, age_col, sex_col).
    Only rows with valid SID, age, sex are returned.
    """
    delim = _sniff_delimiter(csv_path)
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=delim)
        rows = list(reader)

    if not rows:
        raise ValueError("CSV appears empty.")

    header = rows[0]
    has_header = any(not SID_RE.match(c or "") for c in header)  # crude but effective

    def idx_from_name(name: str) -> int:
        lower = [h.strip().lower() for h in header]
        try:
            return lower.index(name.strip().lower())
        except ValueError:
            raise ValueError(f"Column named '{name}' not found in CSV header: {header}")

    if id_name or age_name or sex_name:
        if not has_header:
            raise ValueError("Column names given but CSV seems to have no header row.")
        id_i  = idx_from_name(id_name)  if id_name  else (id_col - 1 if id_col else None)
        age_i = idx_from_name(age_name) if age_name else (age_col - 1 if age_col else None)
        sex_i = idx_from_name(sex_name) if sex_name else (sex_col - 1 if sex_col else None)
    else:
        # Index mode (1-based to match “Column 3/4” phrasing)
        if id_col is None or age_col is None or sex_col is None:
            raise ValueError("Provide either column *names* or 1-based *indices* for id, age, and sex.")
        id_i, age_i, sex_i = id_col - 1, age_col - 1, sex_col - 1

    data_rows = rows[1:] if has_header else rows

    mapping: Dict[str, Tuple[int, int]] = {}
    bad_rows = 0
    for r in data_rows:
        if max(id_i, age_i, sex_i) >= len(r):
            bad_rows += 1
            continue
        sid = (r[id_i] or "").strip()
        if not SID_RE.match(sid):
            bad_rows += 1
            continue

        # Age
        age_raw = (r[age_i] or "").strip()
        try:
            age = int(round(float(age_raw)))
        except Exception:
            # skip NA/missing
            bad_rows += 1
            continue

        # Sex: map generously
        sex_raw = (r[sex_i] or "").strip().lower()
        sex_map = {
            "0": 0, "1": 1,
            "m": 1, "male": 1, "mann": 1, "männlich": 1,
            "f": 0, "w": 0, "female": 0, "frau": 0, "weiblich": 0
        }
        if sex_raw in sex_map:
            sex = sex_map[sex_raw]
        else:
            try:
                v = int(float(sex_raw))
                if v not in (0,1):
                    raise ValueError
                sex = v
            except Exception:
                bad_rows += 1
                continue

        mapping[sid] = (age, sex)

    if not mapping:
        raise ValueError("No usable (ID, age, sex) rows found in CSV.")
    if bad_rows:
        print(f"[warn] Skipped {bad_rows} row(s) with missing/invalid ID/age/sex.")

    return mapping

# ---------- Labels writing ----------

def write_group_labels(for_brainage: Path, relabel: Dict[str, Dict[str, Tuple[int, int]]]):
    """
    relabel: {group -> {sid -> (age, sex)}}
    Writes age_<GROUP>.txt, male_<GROUP>.txt, subjects_<GROUP>.txt under for_brainage/labels/.
    Order = lexicographic by subject ID (matches file order).
    """
    labels_dir = for_brainage / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    for group, sid_map in sorted(relabel.items()):
        if not sid_map:
            continue
        sids_sorted = sorted(sid_map.keys())  # rp1_*.nii sorting matches this
        ages = [str(sid_map[s][0]) for s in sids_sorted]
        males = [str(sid_map[s][1]) for s in sids_sorted]
        (labels_dir / f"subjects_{group}.txt").write_text("\n".join(sids_sorted) + "\n", encoding="utf-8")
        (labels_dir / f"age_{group}.txt").write_text("\n".join(ages) + "\n", encoding="utf-8")
        (labels_dir / f"male_{group}.txt").write_text("\n".join(males) + "\n", encoding="utf-8")
        print(f"[labels] Group {group}: n={len(sids_sorted)} -> age_{group}.txt, male_{group}.txt")

# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser(description="Prepare CAT12 segmentations for BA_data2mat using a CSV list of subjects + labels.")
    ap.add_argument("--root", required=True, help="Root containing T1_CAT12/ and/or T1_CAT12_Kontrollen/")
    ap.add_argument("--csv", required=True, help="CSV with subject IDs and labels")
    # Choose either names OR indices (1-based)
    ap.add_argument("--id-col-name", help="Column name of Subject ID (e.g., 'D')")
    ap.add_argument("--age-col-name", help="Column name of Age")
    ap.add_argument("--sex-col-name", help="Column name of Sex")
    ap.add_argument("--id-col", type=int, default=1, help="1-based column index of Subject ID (default 1)")
    ap.add_argument("--age-col", type=int, default=3, help="1-based column index of Age (default 3)")
    ap.add_argument("--sex-col", type=int, default=4, help="1-based column index of Sex (default 4)")
    ap.add_argument("--release", default="_CAT12.9", help="Release tag (e.g., _CAT12.9)")
    ap.add_argument("--output", default=None, help="Output base directory (default: <root>/for_brainage)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    csv_path = Path(args.csv).resolve()
    out_root = Path(args.output).resolve() if args.output else (root / "for_brainage")
    out_root.mkdir(parents=True, exist_ok=True)

    # Parse CSV into SID -> (age, sex)
    id_name = args.id_col_name
    age_name = args.age_col_name
    sex_name = args.sex_col_name
    if any([id_name, age_name, sex_name]):
        id_col = age_col = sex_col = None
    else:
        id_col, age_col, sex_col = args.id_col, args.age_col, args.sex_col

    sid_to_labels = parse_csv(csv_path, id_col, age_col, sex_col, id_name, age_name, sex_name)
    wanted_sids = set(sid_to_labels.keys())
    print(f"[info] Subjects in CSV: {len(wanted_sids)}")

    # Source directories
    src_patients   = root / "T1_CAT12"
    src_controls   = root / "T1_CAT12_Kontrollen"
    subject_dirs   = find_subject_dirs(src_patients) + find_subject_dirs(src_controls)
    sid_to_dir     = {p.name: p for p in subject_dirs}

    missing_on_disk = sorted([sid for sid in wanted_sids if sid not in sid_to_dir])
    if missing_on_disk:
        print(f"[warn] {len(missing_on_disk)} CSV subject(s) not found on disk (skipped): {', '.join(missing_on_disk[:10])}{' ...' if len(missing_on_disk)>10 else ''}")

    stats: Dict[str, int] = {}
    relabel: Dict[str, Dict[str, Tuple[int, int]]] = {}  # group -> {sid -> (age, sex)}
    errors: List[Tuple[str, str]] = []

    # Copy only the subjects present in the CSV and on disk
    for sid in sorted(wanted_sids):
        if sid not in sid_to_dir:
            continue
        sdir = sid_to_dir[sid]
        try:
            group, _, gm, wm = copy_seg_for_sid(sdir, out_root, args.release)
            stats[group] = stats.get(group, 0) + 1
            relabel.setdefault(group, {})[sid] = sid_to_labels[sid]
            print(f"[copy] {sid}: -> {gm.relative_to(out_root)} ; {wm.relative_to(out_root)}")
        except Exception as e:
            errors.append((sid, str(e)))
            print(f"[ERROR] {sid}: {e}")

    # Write per-group labels matching the file order
    write_group_labels(out_root, relabel)

    print("\n== Summary ==")
    for g in sorted(stats):
        print(f"Group {g}: {stats[g]} subjects")
    if errors:
        print("\nErrors:")
        for sid, msg in errors:
            print(f" - {sid}: {msg}")

if __name__ == "__main__":
    main()
