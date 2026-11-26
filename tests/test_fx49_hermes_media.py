"""Verify the media builder's publication order under Windows file-sharing constraints."""

import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
from types import SimpleNamespace
import zipfile

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "docs/paper-sim/runtime/scripts/windows/hermes/prepare_media.py"
SPEC = importlib.util.spec_from_file_location("fx49_hermes_media", SOURCE)
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def build_case(tmp_path, monkeypatch):
    def archive(name, member):
        path = tmp_path / name
        with tarfile.open(path, "w:gz") as bundle:
            info = tarfile.TarInfo(member)
            info.size = 4
            bundle.addfile(info, io.BytesIO(b"code"))
        return path

    args = SimpleNamespace(source_archive=archive("app.tar.gz", "app/README.md"),
                           python_archive=archive("runtime.tar.gz", "python/python.exe"),
                           launcher=tmp_path / "hermes.exe", wheel_dir=tmp_path, output=tmp_path / "media.zip")
    for name in ["hermes.exe", "example.whl", "fixed_entry.py", "upstream-inputs.json", "windows-requirements.lock"]:
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setattr(media, "HERE", tmp_path)
    monkeypatch.setattr(media, "INPUTS", {
        "hermes_source": {"sha256": media.hash_file(args.source_archive)},
        "cpython": {"sha256": media.hash_file(args.python_archive)},
        "launcher_sha256": media.hash_file(args.launcher),
        "windows_requirements_sha256": digest(b"windows-requirements.lock"),
        "wheels": {"example.whl": digest(b"example.whl")}, "wheel_count": 1,
    })
    return args


@pytest.fixture
def sharing_guard(build_case, monkeypatch):
    partial = build_case.output.with_suffix(".zip.partial")
    writers, events = [], []
    open_path, link_path, unlink_path = Path.open, Path.hardlink_to, Path.unlink
    original_zip = zipfile.ZipFile

    def guard(operation):
        events.append(operation)
        if any(not writer.closed for writer in writers):
            raise PermissionError("archive still open for writing")

    def track_open(path, mode="r", *args, **kwargs):
        result = open_path(path, mode, *args, **kwargs)
        if path == partial and mode == "xb":
            writers.append(result)
        return result

    class GuardedZipFile(original_zip):
        def __init__(self, file, mode="r", *args, **kwargs):
            if file == partial and mode == "r":
                guard("crc read")
            super().__init__(file, mode, *args, **kwargs)

    def link(path, target):
        if target == partial:
            guard("publish")
        return link_path(path, target)

    def unlink(path, *args, **kwargs):
        if path == partial:
            guard("cleanup")
        return unlink_path(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    monkeypatch.setattr(Path, "hardlink_to", link)
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(zipfile, "ZipFile", GuardedZipFile)
    return partial, events


def test_verified_archive_releases_writer_before_read_link_and_cleanup(build_case, sharing_guard):
    partial, events = sharing_guard
    result = media.build(build_case)
    assert events == ["crc read", "publish", "cleanup"]
    assert result["sha256"] == media.hash_file(build_case.output)
    assert not partial.exists()


def test_existing_partial_is_never_modified(build_case, sharing_guard):
    partial, events = sharing_guard
    partial.write_bytes(b"prior build")
    with pytest.raises(FileExistsError):
        media.build(build_case)
    assert events == []
    assert partial.read_bytes() == b"prior build"
    assert not build_case.output.exists()


def test_existing_output_is_preserved_and_only_owned_partial_is_removed(build_case, sharing_guard):
    partial, events = sharing_guard
    build_case.output.write_bytes(b"existing release")
    with pytest.raises(FileExistsError):
        media.build(build_case)
    assert events == ["crc read", "publish", "cleanup"]
    assert build_case.output.read_bytes() == b"existing release"
    assert not partial.exists()


def test_bad_input_midway_raises_original_error_and_cleans_the_closed_partial(build_case, sharing_guard):
    partial, events = sharing_guard
    (build_case.wheel_dir / "example.whl").write_bytes(b"changed wheel")
    with pytest.raises(ValueError, match="wheel hash mismatch"):
        media.build(build_case)
    assert events == ["cleanup"]
    assert not partial.exists()
    assert not build_case.output.exists()


def test_failed_crc_removes_only_private_staging(build_case, sharing_guard, monkeypatch):
    partial, events = sharing_guard
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: "damaged member")
    with pytest.raises(ValueError, match="archive CRC failure"):
        media.build(build_case)
    assert events == ["crc read", "cleanup"]
    assert not partial.exists()
    assert not build_case.output.exists()
