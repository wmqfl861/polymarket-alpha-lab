"""Assemble deterministic, offline Windows install media from verified public archives."""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import zipfile


HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "upstream-inputs.json").read_text())
SOURCE_DIRS = {
    "agent", "tools", "hermes_cli", "gateway", "cron", "providers", "hermes_platform",
    "hermes_agent", "utils", "locales", "plugins", "pm",
}


def hash_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_file(path):
    parts = path.split("/")
    if len(parts) == 1:
        return path.endswith(".py") or path in {"uv.lock", "pyproject.toml", "LICENSE", "README.md"}
    if parts[0] not in SOURCE_DIRS or any(p in {"tests", "__pycache__", "node_modules"} for p in parts):
        return False
    if parts[0] != "plugins":
        return True
    return ((len(parts) == 2 and path.endswith(".py"))
            or path == "plugins/platforms/__init__.py"
            or path.startswith("plugins/platforms/weixin/"))


def build(args):
    expected = ((args.source_archive, INPUTS["hermes_source"]["sha256"]),
                (args.python_archive, INPUTS["cpython"]["sha256"]),
                (args.launcher, INPUTS["launcher_sha256"]),
                (HERE / "windows-requirements.lock", INPUTS["windows_requirements_sha256"]))
    for file, digest in expected:
        if hash_file(file) != digest:
            raise ValueError("input hash mismatch")
    if len(INPUTS["wheels"]) != INPUTS["wheel_count"]:
        raise ValueError("wheel manifest count mismatch")
    part = args.output.with_name(args.output.name + ".partial")
    names = set()
    with part.open("xb") as staging:
        try:
            with zipfile.ZipFile(staging, "w", allowZip64=True) as media:
                def add(name, contents, stored=False):
                    keys = name.split("/")
                    if (name.casefold() in names or any(not k or k in {".", ".."} or ":" in k
                        or "\\" in k or "\x00" in k or k.rstrip(". ") != k for k in keys)):
                        raise ValueError("unsafe or duplicate archive member")
                    names.add(name.casefold())
                    info = zipfile.ZipInfo(name, date_time=(2026, 11, 26, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    method = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
                    media.writestr(info, contents, compress_type=method, compresslevel=3)

                with tarfile.open(args.source_archive, "r|gz") as source:
                    for item in source:
                        _, sep, name = item.name.partition("/")
                        if item.isfile() and sep and source_file(name):
                            add("source/" + name, source.extractfile(item).read())
                with tarfile.open(args.python_archive, "r|gz") as archive:
                    for item in archive:
                        if item.isfile():
                            if not item.name.startswith("python/"):
                                raise ValueError("unexpected Python archive layout")
                            add("runtime/" + item.name[7:], archive.extractfile(item).read())
                for filename, digest in sorted(INPUTS["wheels"].items()):
                    wheel = args.wheel_dir / filename
                    raw = wheel.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != digest:
                        raise ValueError("wheel hash mismatch")
                    add("wheels/" + filename, raw, stored=True)
                for filename in ("fixed_entry.py", "windows-requirements.lock", "upstream-inputs.json"):
                    add(filename, (HERE / filename).read_bytes())
                add("hermes.exe", args.launcher.read_bytes())
            staging.flush()
            with zipfile.ZipFile(part) as check:
                if check.testzip() is not None:
                    raise ValueError("archive CRC failure")
            # Hard links give the completed file its final name atomically and never overwrite it.
            args.output.hardlink_to(part)
        finally:
            part.unlink(missing_ok=True)
    return {"files":len(names),"bytes":args.output.stat().st_size,"sha256":hash_file(args.output)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for option in ("source-archive","python-archive","wheel-dir","launcher","output"):
        parser.add_argument("--"+option,type=Path,required=True)
    print(json.dumps(build(parser.parse_args()),indent=2))
