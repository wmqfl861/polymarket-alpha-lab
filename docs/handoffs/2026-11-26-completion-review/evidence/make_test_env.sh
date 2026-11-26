#!/usr/bin/env bash
# Builds the auditor's offline test environment for one worktree.
# Package installs are not possible in the review environment, so:
#   * pytest comes from an existing local installation (symlinked directory);
#   * the project is made importable the way an editable install does it
#     (a .pth file naming <worktree>/src);
#   * the project's single runtime dependency, the `tzdata` package, is replaced
#     by a stand-in assembled from the operating system's IANA time-zone
#     database (same file format, same directory layout).
# Usage: bash make_test_env.sh <worktree> <venv_dir> <pytest_site_dir> <tz_site_dir>
set -euo pipefail
worktree=$(cd "$1" && pwd); venv=$2; pytest_site=$3; tz_site=$4
if [ ! -d "$tz_site/tzdata" ]; then
  mkdir -p "$tz_site/tzdata"
  cp -rL /usr/share/zoneinfo "$tz_site/tzdata/zoneinfo"
  iana=$(sed -n '1s/^# version //p' /usr/share/zoneinfo/tzdata.zi)
  serial=$(python3 -I -c 'import sys; print(ord(sys.argv[1][-1]) - ord("a") + 1)' "$iana")
  printf '"""Stand-in for the PyPI tzdata package, assembled from /usr/share/zoneinfo."""\n__version__ = "%s.%s"\nIANA_VERSION = "%s"\n' "${iana%?}" "$serial" "$iana" > "$tz_site/tzdata/__init__.py"
  find "$tz_site/tzdata/zoneinfo" -type d -exec sh -c 'for d; do : > "$d/__init__.py"; done' sh {} +
fi
python3.12 -m venv --without-pip "$venv"
site="$venv/lib/python3.12/site-packages"
printf '%s\n' "$(cd "$pytest_site" && pwd)" > "$site/auditor_pytest.pth"
printf '%s\n' "$(cd "$tz_site" && pwd)" > "$site/auditor_tzdata_standin.pth"
printf '%s\n' "$worktree/src" > "$site/auditor_project_src.pth"
"$venv/bin/python" -I -c '
import sys, tzdata, pytest, polymarket_alpha_lab
from importlib import resources
with resources.files("tzdata.zoneinfo.America").joinpath("New_York").open("rb") as f:
    head = f.read(4)
print("python", sys.version.split()[0], "| pytest", pytest.__version__, "| tzdata stand-in", tzdata.__version__, tzdata.IANA_VERSION, "| TZif magic", head, "| project from", polymarket_alpha_lab.__file__)
'
