"""Anonymized-release packager for VESPER-SH.

Double-blind guard: before zipping a dataset root (e.g. `results/vesper_sh`)
for distribution, assert that no author-identifying string appears in any
`.md` or `.json` file under that root. If the check fails, nothing is
zipped and the script exits non-zero with the offending (file, string)
pairs so the leak can be fixed at the source.

Usage:
    python scripts/dataset/package_release.py <root> <dist_dir>

Example:
    python scripts/dataset/package_release.py results/vesper_sh dist/vesper-sh
    # -> dist/vesper-sh/vesper-sh.zip  (or "IDENTITY LEAK: [...]" + exit 1)
"""
import glob
import os
import shutil
import sys

# Case-sensitive substrings that must never appear in a released file.
# "@" catches email addresses and other identity-bearing handles.
FORBIDDEN = ["Chenglong", "Huan", "Bui", "UNCC", "Charlotte", "hbui", "ORCID", "@"]


def check(root):
    """Return a list of (path, forbidden_word) for every match found under
    root's .md and .json files. Empty list == clean."""
    bad = []
    paths = (
        glob.glob(f"{root}/**/*.md", recursive=True)
        + glob.glob(f"{root}/**/*.json", recursive=True)
    )
    for p in paths:
        t = open(p, errors="ignore").read()
        for w in FORBIDDEN:
            if w in t:
                bad.append((p, w))
    return bad


def main(root, dist):
    bad = check(root)
    if bad:
        print("IDENTITY LEAK:", bad[:5])
        sys.exit(1)
    os.makedirs(dist, exist_ok=True)
    archive_base = os.path.join(dist, "vesper-sh")
    shutil.make_archive(archive_base, "zip", root)
    print("packaged ->", archive_base + ".zip")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <root> <dist_dir>")
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
