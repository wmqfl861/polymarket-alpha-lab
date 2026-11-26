"""Build and run the two existing notification callers with the pinned fixed-receipt changes."""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PATCH = HERE / "fixed-receipt.patch"
TRANSPORT = HERE.parent / "scripts" / "weixin_delivery.py"
# Raw site files before and after applying only the registered notification changes.
CALLERS = {
    "football_store.py": ("f0d7ce1ef7e0550b9fa9e6c4d07080943a0999ce9b19caeabe927a3ec1f4ed81",
                          "e133cb1f9aa780a9d96c2b92dc5b1480b5858c661d3320321c5b4be13254272b"),
    "football_daily.py": ("a907dcd25264b039613dece60068ae6fa970488f65a81a3062e01c039afe2ead",
                          "b161d35a2d904d2bcde92418d76ddcbebc67655adfb9c877c9682eeb2c110442"),
}
MAX_SOURCE = 512 * 1024
MAX_BUNDLE = len(CALLERS) * MAX_SOURCE + 4096
GIT_ENV = {"PATH": "/usr/bin:/bin", "GIT_ALLOW_PROTOCOL": "", "GIT_NO_LAZY_FETCH": "1",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}


class EntryError(Exception):
    pass


def read_file(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise EntryError("file_too_large")
    return data


def verify(data, expected):
    if hashlib.sha256(data).hexdigest() != expected:
        raise EntryError("source_digest_mismatch")
    return data


def git(root, *args, stdin=None):
    proc = subprocess.run(["/usr/bin/git", "--no-replace-objects", "-C", str(root), *args],
                          input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env=GIT_ENV, timeout=10, check=False)
    if proc.returncode:
        raise EntryError("git_verification_failed")
    return proc.stdout


def pinned_blob(path):
    commit = os.environ.get("PAL_WEIXIN_RELEASE_COMMIT", "")
    if not re.fullmatch("[0-9a-f]{40}", commit) or git(ROOT, "rev-parse", "--verify", "HEAD") != (commit + "\n").encode():
        raise EntryError("unverified_release")
    data = read_file(path, MAX_SOURCE)
    if git(ROOT, "cat-file", "blob", f"{commit}:{path.relative_to(ROOT).as_posix()}") != data:
        raise EntryError("unverified_release")
    return data


def stage(business_dir, bundle):
    patch = pinned_blob(PATCH)
    with tempfile.TemporaryDirectory(prefix="notification-stage-") as staging:
        staged = Path(staging)
        for name, (before, _) in CALLERS.items():
            (staged / name).write_bytes(verify(read_file(business_dir / name, MAX_SOURCE), before))
        git(staged, "apply", "--no-index", "--whitespace=error-all", "-", stdin=patch)
        with tempfile.NamedTemporaryFile(dir=bundle.parent) as tmp:
            with zipfile.ZipFile(tmp, "w") as archive:
                for name, (_, after) in CALLERS.items():
                    data = verify(read_file(staged / name, MAX_SOURCE), after)
                    archive.writestr(zipfile.ZipInfo(name), data)
            tmp.flush()
            os.fsync(tmp.fileno())
            try:
                os.link(tmp.name, bundle)
            except FileExistsError:
                if read_file(bundle, MAX_BUNDLE) != read_file(Path(tmp.name), MAX_BUNDLE):
                    raise EntryError("different_bundle_already_exists") from None


def read_callers(bundle):
    data = read_file(bundle, MAX_BUNDLE)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) != len(CALLERS) or {m.filename for m in members} != CALLERS.keys():
            raise EntryError("invalid_bundle")
        callers = {}
        for member in members:
            if member.compress_type != zipfile.ZIP_STORED or member.file_size > MAX_SOURCE:
                raise EntryError("invalid_bundle")
            callers[member.filename] = verify(archive.read(member), CALLERS[member.filename][1])
    return callers


def load_verified(name, location, data):
    spec = importlib.util.spec_from_file_location(name, location)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Execute exactly the bytes already verified. No path or bytecode-cache reread.
    exec(compile(data, str(location), "exec", dont_inherit=True), module.__dict__)
    return module


class SiteHelpers(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Import registered application helpers at their original data-relative locations."""

    def __init__(self, business_dir, manifest):
        self.directory = business_dir
        self.hashes = {}
        for line in manifest.splitlines():
            match = re.fullmatch(rb"([0-9a-f]{64})  ([a-z][a-z0-9_]*)\.py", line)
            if not match:
                raise EntryError("invalid_helper_manifest")
            expected, name = (part.decode("ascii") for part in match.groups())
            if name in self.hashes or name == "weixin_delivery" or f"{name}.py" in CALLERS:
                raise EntryError("invalid_helper_manifest")
            self.hashes[name] = expected

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.hashes:
            location = self.directory / f"{fullname}.py"
            return importlib.util.spec_from_file_location(fullname, location, loader=self)
        return None

    def exec_module(self, module):
        data = verify(read_file(Path(module.__file__), MAX_SOURCE), self.hashes[module.__name__])
        exec(compile(data, module.__file__, "exec", dont_inherit=True), module.__dict__)


def run(business_dir, bundle, args):
    if (Path.cwd() != HERE or not business_dir.is_absolute() or not bundle.is_absolute()
            or any(Path(item).resolve() == business_dir for item in sys.path)):
        raise EntryError("unexpected_notification_directory")
    if not args or args[0] not in {"tick", "worker", "route-check", "--help"}:
        raise EntryError("unsupported_notification_command")
    pinned_blob(HERE / "entry.py")
    transport = pinned_blob(TRANSPORT)
    origin = os.environ.get("PAL_WEIXIN_ROUTE_ORIGIN", "")
    if origin not in {"", "kernel-v1"}:
        raise EntryError("unknown_private_route_origin")
    if origin:
        registration_path = HERE / "local_route_registration.py"
        registration_bytes = pinned_blob(registration_path)
    helpers = SiteHelpers(business_dir, pinned_blob(HERE / "site-helpers.sha256"))
    if helpers.hashes.keys() & sys.modules.keys():
        raise EntryError("unexpected_preloaded_helper")
    callers = read_callers(bundle)
    for name, (before, _) in CALLERS.items():
        verify(read_file(business_dir / name, MAX_SOURCE), before)
    # The old scripts directory is never a general module search path. Some helpers
    # locate existing research inputs relative to their own file, so keep their origins.
    sys.meta_path.insert(0, helpers)
    fixed = load_verified("weixin_delivery", TRANSPORT, transport)
    if origin:
        registration = load_verified("_pal_local_route_registration", registration_path, registration_bytes)
        fixed.route = registration.RegisteredRoute(fixed, business_dir)
    load_verified("football_store", bundle / "football_store.py", callers["football_store.py"])
    daily = load_verified("football_daily", bundle / "football_daily.py", callers["football_daily.py"])
    sys.argv = [daily.__file__, *args]
    return daily.main()


def main(args):
    try:
        if len(args) < 4 or args[1] != "--business-dir" or not Path(args[2]).is_absolute():
            raise EntryError("usage_requires_named_business_directory")
        business_dir, bundle = Path(args[2]).resolve(strict=True), Path(args[3])
        if len(args) == 4 and args[0] == "stage":
            stage(business_dir, bundle)
            return 0
        if len(args) >= 5 and args[0] == "run" and sys.flags.isolated:
            return run(business_dir, bundle, args[4:])
        raise EntryError("usage_requires_stage_or_isolated_run")
    except (EntryError, OSError, subprocess.TimeoutExpired, zipfile.BadZipFile) as error:
        # This entry does not expose paths, service environment or business data in diagnostics.
        print(str(error) if isinstance(error, EntryError) else "notification_entry_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
