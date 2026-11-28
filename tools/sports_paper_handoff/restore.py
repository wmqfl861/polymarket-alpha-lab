"""Reconstruct the published small-stake paper pricing sources for offline testing."""

import argparse
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE = "bcce1b17f7263ab89859febfcb56188c295ab371"
NOV_27 = "a56b7d03f73c405685446c475919f63879ed7aaf"
PRICING = Path("src/polymarket_alpha_lab/papersim/sports_paper_pricing.py")
RUNTIME = Path("docs/paper-sim/runtime")
SCRIPTS = RUNTIME / "scripts"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="a new directory under an existing parent")
    parser.add_argument("--release", choices=("2026-11-27", "2026-11-28"), default="2026-11-27",
                        help="the original deployed pricing or the FX-57 reporting candidate")
    args = parser.parse_args()
    dest = args.destination.absolute()
    original = args.release == "2026-11-27"
    release = HERE if original else HERE / "fx57"
    if dest.exists() or dest.is_symlink():
        parser.error("choose a new destination directory")
    if not dest.parent.is_dir():
        parser.error("the destination parent must exist")
    if subprocess.run(["git", "cat-file", "-e", f"{BASE}^{{commit}}"], cwd=REPO,
                      stderr=subprocess.DEVNULL).returncode:
        parser.error("fetch the retained handoff/sports-runtime-20261123 branch (see the handoff)")

    archive = subprocess.check_output([
        "git", "archive", BASE, "--", str(SCRIPTS / "*.py"),
        f":(exclude){SCRIPTS}/weixin_delivery.py", str(RUNTIME / "tests/offline_postgres.py"),
    ], cwd=REPO)
    with TemporaryDirectory(dir=dest.parent) as tmp:
        tree = Path(tmp) / "source"
        tree.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(tree, filter="data")
        subprocess.run(["git", "apply", "--whitespace=error-all", release / "daily-best-ask.patch"],
                       cwd=tree, check=True, env={**os.environ, "GIT_CEILING_DIRECTORIES": tmp})
        scripts = tree / SCRIPTS
        if original:
            module = subprocess.check_output(["git", "show", f"{NOV_27}:{PRICING}"], cwd=REPO)
            (scripts / PRICING.name).write_bytes(module)
        else:
            shutil.copyfile(REPO / PRICING, scripts / PRICING.name)
        for line in (release / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split()
            if hashlib.sha256((scripts / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"reconstructed source differs: {name}")
        # The published models resolve research archives and tools relative to their own tree.
        (tree / "docs/paper-sim/research").symlink_to(REPO / "docs/paper-sim/research", target_is_directory=True)
        (tree / "tools").symlink_to(REPO / "tools", target_is_directory=True)
        tree.rename(dest)
    print(f"Restored and verified {args.release}: eight adapters and the shared pricing module.")


if __name__ == "__main__":
    main()
