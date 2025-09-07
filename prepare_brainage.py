#!/usr/bin/env python3
import argparse
import os
import re
import shutil
from glob import glob
from pathlib import Path
import numpy as np

def split_sid(sid: str) -> tuple[str, str]:
    m = re.match(r"^([A-Za-z]+)(\d+)", sid)
    if not m:
        raise ValueError(f"Subject ID '{sid}' must look like <Letters><Digits>")
    return m.group(1), m.group(2)


# SUBJECT_DIR_PAT = re.compile(r"^[A-Z]\d+$")  # e.g., D01, K12, etc.
SUBJECT_DIR_PAT = re.compile(r"^[A-Za-z]+\d+$")  # e.g., D01, DK01, HC12, K7

def find_subject_dirs(base: Path) -> list[Path]:
    """Return sorted list of subject directories (e.g., D01, K01, …)."""
    if not base.exists():
        return []
    subs = [p for p in base.iterdir() if p.is_dir() and SUBJECT_DIR_PAT.match(p.name)]
    # Lexicographic sort is what SPM uses; D01..D10 will sort correctly if zero-padded.
    subs.sort(key=lambda p: p.name)
    return subs

def copy_seg(subject_dir: Path, out_root: Path, release: str):
    """
    Copy mwp1/mwp2 segmentations from subject_dir/<ID>/mri into:
      out_root/<GroupLetter>/rp1_<release>/rp1_<ID>_T1.nii
      out_root/<GroupLetter>/rp2_<release>/rp2_<ID>_T1.nii
    """
    sid = subject_dir.name  # e.g., D01 or K07
    # group = sid[0]          # 'D' or 'K' (generalized)
    group, _ = split_sid(sid)
    mri_dir = subject_dir / "mri"
    if not mri_dir.is_dir():
        raise FileNotFoundError(f"Missing mri/ for {sid}: {mri_dir}")

    # Accept .nii or .nii.gz (prefer .nii if both exist)
    def find_one(prefix: str) -> Path:
        candidates = sorted(glob(str(mri_dir / f"{prefix}{sid}_T1.nii"))) + \
                     sorted(glob(str(mri_dir / f"{prefix}{sid}_T1.nii.gz")))
        if not candidates:
            raise FileNotFoundError(f"Missing {prefix}{sid}_T1.nii[.gz] under {mri_dir}")
        # Prefer uncompressed NIfTI if present
        for c in candidates:
            if c.endswith(".nii"):
                return Path(c)
        return Path(candidates[0])

    src_gm = find_one("mwp1")  # GM
    src_wm = find_one("mwp2")  # WM

    # Destinations
    rp1_dir = out_root / group / f"rp1{release}"
    rp2_dir = out_root / group / f"rp2{release}"
    rp1_dir.mkdir(parents=True, exist_ok=True)
    rp2_dir.mkdir(parents=True, exist_ok=True)

    dst_gm = rp1_dir / f"rp1_{sid}_T1.nii"
    dst_wm = rp2_dir / f"rp2_{sid}_T1.nii"

    # If source is .nii.gz, decompress by copying to .nii (SPM will read .nii fine)
    def copy_as_nii(src: Path, dst: Path):
        if src.suffix == ".gz":
            # simple gunzip without dependencies
            import gzip
            with gzip.open(src, "rb") as f_in, open(dst, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copy2(src, dst)

    copy_as_nii(src_gm, dst_gm)
    copy_as_nii(src_wm, dst_wm)

    return group, sid, dst_gm, dst_wm

def fake_label(for_brainage: Path, seed: int = 42, mean_age: float = 85.0, std_age: float = 6.0,
               min_age: int = 50, max_age: int = 100):
    """
    Create fake label text files per group (D, K, …) under for_brainage/fake_labels/.
    One integer age per line (N~Normal(mean_age, std_age), clipped), and sex 0/1 per line.
    Ordering matches lexicographically sorted rp1 files for each group.
    """
    rng = np.random.default_rng(seed)
    labels_dir = for_brainage / "fake_labels"
    labels_dir.mkdir(parents=True, exist_ok=True)

    # groups = [p.name for p in for_brainage.iterdir() if p.is_dir() and len(p.name) == 1 and p.name.isalpha()]
    groups = [
        p.name for p in for_brainage.iterdir()
        if p.is_dir() and p.name.isalpha() and p.name.lower() != "fake_labels"
    ]
    groups.sort()
    for g in groups:
        rp1_dir = for_brainage / g / next((d.name for d in (for_brainage / g).iterdir()
                                           if d.is_dir() and d.name.startswith("rp1")), f"rp1_dummy")
        if not rp1_dir.exists():
            # no data for this group, skip
            continue
        # Get subjects in SPM-like order by file name
        rp1_files = sorted([p for p in rp1_dir.glob("rp1_*.nii") if p.is_file()], key=lambda p: p.name)
        n = len(rp1_files)
        if n == 0:
            continue

        # Derive subject IDs for traceability
        subjects = [re.sub(r"^rp1_(.+?)_T1\.nii$", r"\1", p.name) for p in rp1_files]

        ages = rng.normal(loc=mean_age, scale=std_age, size=n)
        ages = np.clip(np.round(ages), min_age, max_age).astype(int)
        sexes = rng.integers(low=0, high=2, size=n)  # 0/1

        # Save
        (labels_dir / f"subjects_{g}.txt").write_text("\n".join(subjects) + "\n", encoding="utf-8")
        np.savetxt(labels_dir / f"age_{g}.txt", ages, fmt="%d")
        np.savetxt(labels_dir / f"male_{g}.txt", sexes, fmt="%d")

        print(f"[fake_label] Group {g}: n={n} -> age_{g}.txt, male_{g}.txt")

def main():
    ap = argparse.ArgumentParser(description="Prepare CAT12 segmentations for BA_data2mat.")
    ap.add_argument("--root", required=True, help="Root directory containing T1_CAT12/ and T1_CAT12_Kontrollen/")
    ap.add_argument("--release", default="_CAT12.9", help="Release tag used by BA_data2mat (e.g., _CAT12.9)")
    ap.add_argument("--output", default=None, help="Output base directory (default: <root>/for_brainage)")
    ap.add_argument("--seed", type=int, default=42, help="Seed for fake labels")
    ap.add_argument("--age-mean", type=float, default=81.0)
    ap.add_argument("--age-std", type=float, default=3.0)
    ap.add_argument("--min-age", type=int, default=50)
    ap.add_argument("--max-age", type=int, default=100)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    src_patients = root / "T1_CAT12"
    src_controls = root / "T1_CAT12_Kontrollen"
    out_root = Path(args.output).resolve() if args.output else (root / "for_brainage")
    out_root.mkdir(parents=True, exist_ok=True)

    patient_dirs = find_subject_dirs(src_patients)
    control_dirs = find_subject_dirs(src_controls)

    if not patient_dirs and not control_dirs:
        raise SystemExit("No subject directories found. Check --root path and naming (e.g., D01, K01).")

    # Copy all, remembering simple stats
    stats = {"D": 0, "K": 0}
    errors = []
    for sdir in patient_dirs + control_dirs:
        try:
            group, sid, gm, wm = copy_seg(sdir, out_root, args.release)
            stats[group] = stats.get(group, 0) + 1
            print(f"[copy] {sid}: -> {gm.relative_to(out_root)} ; {wm.relative_to(out_root)}")
        except Exception as e:
            errors.append((sdir, str(e)))
            print(f"[ERROR] {sdir}: {e}")

    # Fake labels
    fake_label(out_root, seed=args.seed, mean_age=args.age_mean, std_age=args.age_std,
               min_age=args.min_age, max_age=args.max_age)

    print("\n== Summary ==")
    for g, n in sorted(stats.items()):
        print(f"Group {g}: {n} subjects")
    if errors:
        print("\nErrors:")
        for sdir, msg in errors:
            print(f" - {sdir}: {msg}")

if __name__ == "__main__":
    main()
