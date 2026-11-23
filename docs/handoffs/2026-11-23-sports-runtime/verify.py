#!/usr/bin/env python3
"""Verify the sports runtime handoff in a Linux Git checkout."""

import configparser
import hashlib
import os
import re
import shlex
import stat
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path, PurePosixPath


HANDOFF = PurePosixPath("docs/handoffs/2026-11-23-sports-runtime")
RUNTIME = PurePosixPath("docs/paper-sim/runtime")
HASHES = HANDOFF / "MANIFEST.sha256"
SIZES = HANDOFF / "SIZES.tsv"
VERIFIER = HANDOFF / "verify.py"
SCRIPT_PREFIX = "@PAL_RUNTIME_ROOT@/scripts/"
POSTGRES_UNIT = "pal-football-notify-pg.service"


def relative_path(name):
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or path.as_posix() != name
        or ".." in path.parts
        or "\\" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise ValueError(f"Invalid repository-relative path {name!r}")
    return path


def tracked_files(root):
    result = subprocess.run(
        ["git", "-C", root, "ls-files", "--stage", "-z", "--", str(HANDOFF), str(RUNTIME)],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"Cannot read Git index ({result.stderr.decode(errors='replace').strip()})")
    paths = set()
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, _, stage = metadata.split()
        path = relative_path(name.decode("utf-8"))
        if mode not in {b"100644", b"100755"} or stage != b"0" or path in paths:
            raise ValueError(f"Expected one regular Git index entry for {path}")
        paths.add(path)
    return paths


def read_file(root, path):
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        with ExitStack() as files:
            directory = os.open(root, directory_flags)
            files.callback(os.close, directory)
            for part in path.parts[:-1]:
                directory = os.open(part, directory_flags, dir_fd=directory)
                files.callback(os.close, directory)
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            files.callback(os.close, descriptor)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError(f"Expected a regular file at {path}")
            with open(descriptor, "rb", closefd=False) as stream:
                return stream.read()
    except OSError as error:
        raise ValueError(f"Cannot read {path} ({error.strerror})") from error


def read_manifest(root, path, row_pattern, *, header=None):
    rows = read_file(root, path).decode("utf-8").removesuffix("\n").split("\n")
    first_line = 1
    if header is not None:
        if rows[0] != header:
            raise ValueError(f"Invalid header in {path}")
        rows = rows[1:]
        first_line = 2
    manifest = {}
    for line_number, row in enumerate(rows, first_line):
        match = re.fullmatch(row_pattern, row)
        if match is None:
            raise ValueError(f"Malformed row in {path} at line {line_number}")
        member = relative_path(match["path"])
        if member in manifest:
            raise ValueError(f"Duplicate path {member} in {path} at line {line_number}")
        manifest[member] = match["value"].lower()
    return manifest


def check_inventory(path, manifest, expected):
    missing = sorted(expected - manifest.keys())
    extra = sorted(manifest.keys() - expected)
    if missing or extra:
        details = [f"missing {', '.join(map(str, missing))}"] if missing else []
        if extra:
            details.append(f"unexpected {', '.join(map(str, extra))}")
        raise ValueError(f"{path} differs from the Git inventory ({', '.join(details)})")


def check_units(units, scripts):
    services = {name for name in units if name.endswith(".service")}
    for name, data in units.items():
        # systemd allows a directive such as Environment to appear multiple times.
        unit = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=False)
        unit.optionxform = str
        unit.read_string(data.decode("utf-8"), source=name)
        if name.endswith(".timer"):
            if not unit.has_section("Timer"):
                raise ValueError(f"Missing [Timer] section in {name}")
            default_service = name.removesuffix(".timer") + ".service"
            target = unit.get("Timer", "Unit", fallback=default_service)
            template = re.fullmatch(r"([^@/\s]+)@[^@/\s]+\.service", target)
            if target not in services and (template is None or f"{template[1]}@.service" not in services):
                raise ValueError(f"{name} refers to a missing service {target!r}")
            continue

        command = shlex.split(unit.get("Service", "ExecStart", fallback=""))
        if name == POSTGRES_UNIT:
            if command[:1] != ["@PAL_POSTGRES@"]:
                raise ValueError(f"Missing configured PostgreSQL entry point in {name}")
            continue

        if len(command) < 3 or command[:2] != ["@PAL_PYTHON@", "-B"]:
            raise ValueError(f"Missing configured Python entry point in {name}")
        target = command[2]
        if not target.startswith(SCRIPT_PREFIX) or target.removeprefix(SCRIPT_PREFIX) not in scripts:
            raise ValueError(f"{name} refers to a missing packaged script {target!r}")


def main():
    script_path = Path(os.path.abspath(__file__))
    if not script_path.as_posix().endswith(f"/{VERIFIER}"):
        raise ValueError(f"Run {VERIFIER} from its location in a Git checkout")
    root = script_path.parents[len(VERIFIER.parts) - 1]
    tracked = tracked_files(root)
    for path in (HASHES, SIZES, VERIFIER):
        if path not in tracked:
            raise ValueError(f"Missing tracked file {path}")

    expected = tracked - {HASHES, SIZES}
    hashes = read_manifest(root, HASHES, r"(?P<value>[0-9a-fA-F]{64})  (?P<path>.+)")
    sizes = read_manifest(root, SIZES, r"(?P<path>[^\t]+)\t(?P<value>0|[1-9][0-9]*)", header="path\tbytes")
    check_inventory(HASHES, hashes, expected)
    check_inventory(SIZES, sizes, expected)

    scripts = set()
    units = {}
    compiled = 0
    for path in sorted(expected):
        data = read_file(root, path)
        if str(len(data)) != sizes[path]:
            raise ValueError(f"Byte size mismatch for {path}")
        if hashlib.sha256(data).hexdigest() != hashes[path]:
            raise ValueError(f"SHA-256 mismatch for {path}")
        if path.suffix == ".py":
            compile(data, str(path), "exec", dont_inherit=True)
            compiled += 1
            if path.parent == RUNTIME / "scripts":
                scripts.add(path.name)
        if path.parent == RUNTIME / "systemd" and path.suffix in {".service", ".timer"}:
            units[path.name] = data

    if len(scripts) != 27 or len(units) != 16:
        raise ValueError(f"Expected 27 runtime scripts and 16 systemd units, found {len(scripts)} and {len(units)}")
    check_units(units, scripts)
    print(f"Verified SHA-256 and byte sizes for {len(expected)} tracked handoff and runtime files.")
    print(f"Compiled {compiled} Python files in memory, including {len(scripts)} runtime scripts.")
    print(f"Checked entry points for {len(units)} systemd units.")


if __name__ == "__main__":
    try:
        main()
    except (configparser.Error, OSError, SyntaxError, ValueError) as error:
        sys.exit(f"Verification failed: {error}")
