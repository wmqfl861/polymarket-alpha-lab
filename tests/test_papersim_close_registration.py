import datetime

import pytest

from polymarket_alpha_lab.papersim import store
from polymarket_alpha_lab.papersim.d1compare import close_registration


def test_registration_invalid_scope_never_opens_a_connection(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid input reached a working tree or database")

    monkeypatch.setattr(close_registration, "require_clean_worktree", unexpected)
    monkeypatch.setattr(close_registration.store_psycopg, "connect_managed", unexpected)
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    for team, start, end, code in [
            (" ", t, t + datetime.timedelta(days=1), "d1_close_window_team_invalid"),
            ("research", t, t, "d1_close_window_range_invalid"),
            ("research", t, t - datetime.timedelta(days=1), "d1_close_window_range_invalid"),
            ("research", t.replace(tzinfo=None), t, "d1_operation_time_invalid")]:
        with pytest.raises(store.PapersimStoreError, match="^" + code + "$"):
            close_registration.register(repo_root=tmp_path, team=team, starts_at=start, ends_at=end)


def test_invalid_book_age_inputs_are_refused_before_opening_a_connection(monkeypatch, tmp_path):
    from polymarket_alpha_lab.papersim import prepare

    def unexpected(*args, **kwargs):
        pytest.fail("invalid policy reached a working tree or database")

    monkeypatch.setattr(close_registration, "require_clean_worktree", unexpected)
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    args = dict(repo_root=tmp_path, team="research", starts_at=start, ends_at=start + datetime.timedelta(hours=1))
    for source_age, receipt_age in [(True, 1), (1, False), (None, 1), (0, -1), ("1", 2)]:
        with pytest.raises(prepare.PreparationError, match="^d1_capture_book_age_policy_invalid$"):
            close_registration.register_book_age(**args, max_source_age_seconds=source_age,
                                                 max_receipt_age_seconds=receipt_age, basis_sha256="a" * 64)
    with pytest.raises(store.PapersimStoreError, match="^papersim_snapshot_hash_invalid$"):
        close_registration.register_book_age(**args, max_source_age_seconds=0, max_receipt_age_seconds=1,
                                             basis_sha256="missing")
