"""Tests for ``python -m miniagent --stop`` argument parsing helpers."""

from __future__ import annotations

from miniagent.__main__ import _argv_after_flag, _parse_stop_target_ids


def test_argv_after_stop_includes_all_flag():
    assert _argv_after_flag(["-m", "miniagent", "--stop", "--all"], "--stop") == ["--all"]
    assert _argv_after_flag(["-m", "miniagent", "--stop", "1", "2"], "--stop") == ["1", "2"]
    assert _argv_after_flag(["-m", "miniagent", "--stop"], "--stop") == []


def test_parse_stop_all():
    v = {1, 3, 5}
    ids, err = _parse_stop_target_ids(["--all"], v)
    assert err is None
    assert ids == [1, 3, 5]


def test_parse_stop_all_lowercase():
    ids, err = _parse_stop_target_ids(["all"], {2})
    assert err is None
    assert ids == [2]


def test_parse_stop_numeric_ids_order():
    ids, err = _parse_stop_target_ids(["5", "1"], {1, 5, 7})
    assert err is None
    assert ids == [5, 1]


def test_parse_stop_dedup():
    ids, err = _parse_stop_target_ids(["3", "3", "1"], {1, 3})
    assert err is None
    assert ids == [3, 1]


def test_parse_stop_unknown_id():
    _, err = _parse_stop_target_ids(["99"], {1})
    assert err is not None
    assert "不在" in err


def test_parse_stop_bad_token():
    _, err = _parse_stop_target_ids(["--feishu"], {1})
    assert err is not None


def test_parse_stop_invalid_number():
    _, err = _parse_stop_target_ids(["x"], {1})
    assert err is not None
