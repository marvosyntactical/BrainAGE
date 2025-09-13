#!/usr/bin/env python3
import argparse
import csv
import gzip
import re
import shutil
from glob import glob
from pathlib import Path
from typing import Dict, List, Tuple

# SID_RE = re.compile(r"^([A-Za-z]+)(\d+)$")  # e.g., D01, FD02, K9
SID_RE = re.compile(r"^([A-Za-z]+)(\d+[A-Za-z]?)$") # e.g., D01, FD02, K9, K11a, etc

# ---------- ID parsing ----------
def split_sid(sid: str) -> Tuple[str, str]:
    # m = SID_RE.match(sid)
    m = re.match(r"^([A-Za-z]+)(\d+[A-Za-z]?)$", sid)
    if not m:
        raise ValueError(f"Subject ID '{sid}' must look like <Letters><Digits> (e.g., D01, FD03)")
    return m.group(1), m.group(2)  # (group, number)

# ---------- filesystem helpers ----------
def find_subject_dirs(base: Path) -> List[Path]:
    if not base.exists():
        return []
    subs = [p for p in base.iterdir() if p.is_dir() and SID_RE.match(p.name)]
    subs.sort(key=lambda p: p.name)  # SPM-like lexicographic order
    return subs

def copy_seg_for_sid(subject_dir: Path, out_root: Path, release: str) -> Tuple[str, str, Path, Path]:
    sid = subject_dir.name
    group, _ = split_sid(sid)
    mri_dir = subject_dir / "mri"
    if not mri_dir.is_dir():
        raise FileNotFoundError(f"Missing mri/ for {sid}: {mri_dir}")

    def pick(prefix: str) -> Path:
        cands = sorted(glob(str(mri_dir / f"{prefix}{sid}_T1.nii"))) + \
                sorted(glob(str(mri_dir / f"{prefix}{sid}_T1.nii.gz")))
        if not cands:
            raise FileNotFoundError(f"Missing {prefix}{sid}_T1.nii[.gz] in {mri_dir}")
        for c in cands:
            if c.endswith(".nii"):
                return Path(c)
        return Path(cands[0])

    src_gm = pick("mwp1")  # GM
    src_wm = pick("mwp2")  # WM

    rp1_dir = out_root / group / f"rp1{release}"
    rp2_dir = out_root / group / f"rp2{release}"
    rp1_dir.mkdir(parents=True, exist_ok=True)
    rp2_dir.mkdir(parents=True, exist_ok=True)

    dst_gm = rp1_dir / f"rp1_{sid}_T1.nii"
    dst_wm = rp2_dir / f"rp2_{sid}_T1.nii"

    def copy_as_nii(src: Path, dst: Path):
        if src.suffix == ".gz":
            with gzip.open(src, "rb") as fin, open(dst, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        else:
            shutil.copy2(src, dst)

    copy_as_nii(src_gm, dst_gm)
    copy_as_nii(src_wm, dst_wm)
    return group, sid, dst_gm, dst_wm

# ---------- CSV parsing tailored to German headers ----------
def _sniff_delim(p: Path) -> str:
    txt = p.read_bytes()[:65536].decode("utf-8", errors="ignore")
    try:
        return csv.Sniffer().sniff(txt, delimiters=";,|\t,").delimiter
    except Exception:
        return ";"

def _find_col(header: List[str], candidates: List[str]) -> int:
    low = [h.strip().lower() for h in header]
    for i, h in enumerate(low):
        for cand in candidates:
            if cand in h:  # substring match (handles 'Geschlecht (1=m, 2=f)')
                return i
    raise ValueError(f"Could not find any of {candidates} in header: {header}")

def parse_csv_german(csv_path: Path) -> Dict[str, Tuple[int, int]]:
    """
    Expect columns like: 'Code', 'Alter', 'Geschlecht (1=m, 2=f)'.
    Ignores group header rows; keeps only rows where 'Code' matches <Letters><Digits>.
    Returns mapping SID -> (age_int, male_flag) with male=1, female=0.
    """
    delim = _sniff_delim(csv_path)
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter=delim))

    if not rows:
        raise ValueError("CSV is empty.")

    header = rows[0]
    has_header = True
    code_i = _find_col(header, ["code"])
    age_i  = _find_col(header, ["alter", "age"])
    sex_i  = _find_col(header, ["geschlecht", "sex"])

    data = rows[1:] if has_header else rows
    out: Dict[str, Tuple[int, int]] = {}
    skipped = 0

    for r in data:
        # if max(code_i, age_i, sex_i) >= len(r):
        #     skipped += 1; continue
        sid = (r[code_i] or "").strip()
        if not SID_RE.match(sid):  # skip group headers or blanks
            continue

        # Age
        age_s = (r[age_i] or "").strip().replace(",", ".")
        age = int(round(float(age_s)))
        # try:
        #     age = int(round(float(age_s)))
        # except Exception:
        #     skipped += 1; continue

        # Sex mapping: sheet uses 1=m, 2=f
        sx = (r[sex_i] or "").strip().lower()
        male = None
        if sx in {"1", "m", "male", "mann", "männlich"}:
            male = 1
        elif sx in {"0", "2", "f", "female", "frau", "weiblich"}:
            male = 0
        else:
            # try numeric
            try:
                v = int(float(sx))
                if v == 1: male = 1
                elif v == 2: male = 0
            except Exception:
                pass
        # if male is None:
        #     skipped += 1; continue

        out[sid] = (age, male)

    if not out:
        raise ValueError("No valid (Code/Age/Sex) rows parsed from CSV.")
    if skipped:
        print(f"[warn] Skipped {skipped} row(s) with invalid/missing values.")
    return out

# ---------- Label writing ----------
def write_group_labels(for_brainage: Path, relabel: Dict[str, Dict[str, Tuple[int, int]]], subdir="labels"):
    labels_dir = for_brainage / subdir
    labels_dir.mkdir(parents=True, exist_ok=True)
    for group, sid_map in sorted(relabel.items()):
        if not sid_map:
            continue
        sids = sorted(sid_map.keys())                 # matches rp1_*.nii sort
        ages = [str(sid_map[s][0]) for s in sids]
        males = [str(sid_map[s][1]) for s in sids]    # 1=male, 0=female
        (labels_dir / f"subjects_{group}.txt").write_text("\n".join(sids) + "\n", encoding="utf-8")
        (labels_dir / f"age_{group}.txt").write_text("\n".join(ages) + "\n", encoding="utf-8")
        (labels_dir / f"male_{group}.txt").write_text("\n".join(males) + "\n", encoding="utf-8")
        print(f"[labels] {group}: n={len(sids)}")

# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser(description="Prepare CAT12 segmentations for BA_data2mat from a German-labeled CSV.")
    ap.add_argument("--root", required=True, help="Root containing T1_CAT12/ and/or T1_CAT12_Kontrollen/")
    ap.add_argument("--csv", required=True, help="CSV with columns like: Code, Alter, Geschlecht (1=m, 2=f)")
    ap.add_argument("--release", default="_CAT12.9", help="Release tag used in folder names (e.g., _CAT12.9)")
    ap.add_argument("--output", default=None, help="Output base directory (default: <root>/for_brainage)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    out_root = Path(args.output).resolve() if args.output else (root / "for_brainage")
    out_root.mkdir(parents=True, exist_ok=True)

    # Parse CSV
    sid2lab = parse_csv_german(Path(args.csv).resolve())
    wanted = set(sid2lab.keys())
    print(f"[info] CSV subjects: {len(wanted)}")

    # Find all available subject dirs
    src_pat = root / "T1_CAT12"
    src_ctl = root / "T1_CAT12_Kontrollen"
    sid2dir = {p.name: p for p in find_subject_dirs(src_pat) + find_subject_dirs(src_ctl)}

    missing = sorted([s for s in wanted if s not in sid2dir])
    if missing:
        print(f"[warn] {len(missing)} CSV subject(s) not found on disk (skipped): {', '.join(missing[:12])}{' ...' if len(missing)>12 else ''}")

    # Copy and build per-group relabel map
    stats: Dict[str, int] = {}
    relabel: Dict[str, Dict[str, Tuple[int, int]]] = {}

    for sid in sorted(wanted):
        if sid not in sid2dir:
            continue
        try:
            group, _, gm, wm = copy_seg_for_sid(sid2dir[sid], out_root, args.release)
            stats[group] = stats.get(group, 0) + 1
            relabel.setdefault(group, {})[sid] = sid2lab[sid]
            print(f"[copy] {sid}: -> {gm.relative_to(out_root)} ; {wm.relative_to(out_root)}")
        except Exception as e:
            print(f"[ERROR] {sid}: {e}")

    write_group_labels(out_root, relabel, subdir="labels")

    print("\n== Summary ==")
    for g in sorted(stats):
        print(f"Group {g}: {stats[g]} subjects")

if __name__ == "__main__":
    main()

