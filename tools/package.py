"""
Build the release zip Decky installs: out/FrostDeck.zip, one FrostDeck/ folder
holding the built frontend, the backend and defaults/.

    pnpm run build
    python tools/package.py                     # python3 on macOS
    python tools/package.py --curseforge-key K  # also ship an approved project key

Only files tracked by git are packaged (plus dist/index.js), so untracked
files and personal keys can't end up in a release. File modes come from git,
so defaults/bnet-launch.sh stays executable when packaged on Windows.
"""

import argparse
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "FrostDeck"
FOLDERS = ("py_modules", "defaults")   # assets/ is README artwork, not needed at runtime
FILES = ("main.py", "plugin.json", "package.json", "LICENSE", "README.md")


def tracked_files() -> dict[str, int]:
    """path -> mode for the packaged folders and files, as git has them."""
    output = subprocess.run(["git", "ls-files", "-s", "--", *FOLDERS, *FILES], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    files: dict[str, int] = {}
    for line in output.splitlines():
        meta, path = line.split("\t", 1)
        files[path] = int(meta.split()[0], 8)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curseforge-key", metavar="FILE", help="approved CurseForge project key to ship as defaults/curseforge.key")
    args = parser.parse_args()

    bundle = os.path.join(ROOT, "dist", "index.js")
    if not os.path.isfile(bundle):
        print("dist/index.js is missing; run `pnpm run build` first", file=sys.stderr)
        return 1
    newest_source = max(os.path.getmtime(os.path.join(dirpath, name)) for dirpath, _, names in os.walk(os.path.join(ROOT, "src")) for name in names)
    if os.path.getmtime(bundle) < newest_source:
        print("dist/index.js is older than src/; run `pnpm run build` first", file=sys.stderr)
        return 1
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *FOLDERS, *FILES, "src"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    if dirty.strip():
        print(f"Warning: uncommitted changes are being packaged:\n{dirty}", file=sys.stderr)

    with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as handle:
        version = json.load(handle)["version"]

    entries = tracked_files()
    entries["dist/index.js"] = 0o100644
    if args.curseforge_key:
        with open(args.curseforge_key, encoding="utf-8") as handle:
            if not handle.read().strip():
                print("The CurseForge key file is empty", file=sys.stderr)
                return 1

    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    target = os.path.join(ROOT, "out", f"{NAME}.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        folders = sorted({os.path.dirname(path) for path in entries} - {""})
        for folder in ["", *folders]:
            info = zipfile.ZipInfo(f"{NAME}/{folder}/".replace("//", "/"))
            info.external_attr = (0o40755 << 16) | 0x10
            archive.writestr(info, b"")
        for path, mode in sorted(entries.items()):
            info = zipfile.ZipInfo(f"{NAME}/{path}")
            info.external_attr = ((mode & 0o777) | 0o100000) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(os.path.join(ROOT, path), "rb") as handle:
                archive.writestr(info, handle.read())
        if args.curseforge_key:
            info = zipfile.ZipInfo(f"{NAME}/defaults/curseforge.key")
            info.external_attr = 0o100644 << 16
            with open(args.curseforge_key, "rb") as handle:
                archive.writestr(info, handle.read())

    key_note = " with a CurseForge key" if args.curseforge_key else " (no CurseForge key)"
    print(f"Built out/{NAME}.zip for {NAME} {version}{key_note}: {len(entries)} files, {os.path.getsize(target):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
