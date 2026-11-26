"""Managed preparation window replay under the original and smaller Gamma layouts."""

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab.papersim import prepare, prepare_inputs
from polymarket_alpha_lab.papersim.adapters import polymarket
from polymarket_alpha_lab.papersim.d1compare import acquire, frozenexec, journal
from tests.test_papersim_capture_marks_native import _D1_NATIVE, _d1_child
from tests.test_papersim_prepared_scan_native import _capture_setup, separate_project

pytestmark = _D1_NATIVE


@pytest.mark.parametrize("size", [None, 3], ids=["historical", "smaller"])
def test_prepared_page_size_replay_and_conflicts(separate_project, size):
    case = _capture_setup(separate_project, f"gamma-layout-{size}", tag="epl", gamma_page_size=size)
    root = case.project.root
    receipt = case.receipt
    manifest, _ = acquire.read_capture(repo_root=root, batch_sha256=receipt["batch_sha256"])
    assert manifest["scope"].get("gamma_page_size") == size
    limit = size or polymarket.GAMMA_EVENTS_PAGE_LIMIT
    gamma = [row["request_meta"]["query"] for row in manifest["requests"]
             if row["source_kind"] == polymarket.SOURCE_KIND_GAMMA]
    assert [(q["limit"], q["offset"]) for q in gamma] == [(str(limit), "0"), (str(limit), str(limit))]
    result = prepare_inputs.resolve_preparation_inputs(
        repo_root=root, config_bytes=case.config.read_bytes(), input_set_sha256=receipt["input_set_sha256"])
    assert result.input_set_sha256 == receipt["input_set_sha256"]
    entrypoint = "import sys; from tools.d1_compare import main; raise SystemExit(main(sys.argv[1:]))"
    invocation = ["capture-inputs", "--repo-root", str(root),
                  "--tag-slug", "epl", "--window-start", receipt["window_start"],
                  "--window-end", receipt["window_end"], "--prepare-config", str(case.config)]
    other_layout = ["--gamma-page-size", "3"] if size is None else []
    conflict = _d1_child(case.project, [*invocation, *other_layout], code=entrypoint)
    assert conflict.returncode != 0
    assert "d1_capture_receipt_conflict" in conflict.stderr
    original_layout = ["--gamma-page-size", str(size)] if size is not None else []
    replay = _d1_child(case.project, [*invocation, *original_layout], code=entrypoint)
    assert replay.returncode == 0
    replayed = json.loads(replay.stdout)
    assert replayed["reused"]
    assert replayed["scope_sha256"] == receipt["scope_sha256"]
    assert replayed["batch_sha256"] == receipt["batch_sha256"]
    assert replayed["input_set_sha256"] == receipt["input_set_sha256"]


def test_excess_events_retain_the_http_body_without_contracts_or_complete_coverage(separate_project):
    case = _capture_setup(separate_project, "gamma-overreturned", tag="epl", gamma_page_size=1,
                          overreturn_gamma_page=True)
    root = case.project.root
    receipt = case.receipt
    manifest, entries = acquire.read_capture(repo_root=root, batch_sha256=receipt["batch_sha256"])
    request, = manifest["requests"]
    entry, = entries
    assert request["http_status"] == 200 and request["status"] == "failed"
    assert request["content_sha256"] == sha256(entry.content).hexdigest()
    assert len(journal.decode_public_json(entry.content)) == 2
    assert request["reason_code"] == acquire.GAMMA_PAGE_SIZE_EXCEEDED
    assert request["page"] is None
    tag, = manifest["coverage"]["tags"]
    assert not tag["coverage_complete"] and tag["reason_code"] == acquire.GAMMA_PAGE_SIZE_EXCEEDED
    assert not manifest["coverage"]["polymarket_transport_complete"]
    assert manifest["markets"] == [] and manifest["books"] == []
    assert frozenexec._gamma_observations(request, entry, None) == (
        {"request_sequence": 1, "page_status": "page_size_exceeded", "events": []}, [])
    resolved = prepare_inputs.resolve_preparation_inputs(
        repo_root=root, config_bytes=case.config.read_bytes(), input_set_sha256=receipt["input_set_sha256"])
    assert not resolved.targets

    # A reader must independently reject a request which hides the excess.
    changed = deepcopy(request)
    changed.update(reason_code=None, status="received", page=journal.gamma_page_index(entry.content))
    view = SimpleNamespace(manifest={**manifest, "requests": [changed]},
                           responses={1: SimpleNamespace(entry=entry)})
    with pytest.raises(prepare.PreparationError, match="papersim_prepare_capture_request_invalid"):
        prepare_inputs._verify_capture_requests(None, view, None, ["epl"])
