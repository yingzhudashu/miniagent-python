"""Tests for ``python -m miniagent --stop`` argument parsing helpers."""

from __future__ import annotations

import pytest

from miniagent.__main__ import (
    _argv_after_flag,
    _extract_stop_state_dir,
    _instance_targets,
    _parse_stop_target_ids,
)


def test_argv_after_stop_includes_all_flag():
    assert _argv_after_flag(["-m", "miniagent", "--stop", "--all"], "--stop") == ["--all"]
    assert _argv_after_flag(["-m", "miniagent", "--stop", "1", "2"], "--stop") == ["1", "2"]
    assert _argv_after_flag(["-m", "miniagent", "--stop"], "--stop") == []


def test_extract_stop_state_dir():
    sd, rest = _extract_stop_state_dir(["--state-dir", "/tmp/ws", "1", "2"])
    assert sd == "/tmp/ws"
    assert rest == ["1", "2"]

    sd, rest = _extract_stop_state_dir(["--all"])
    assert sd is None
    assert rest == ["--all"]


def test_extract_stop_state_dir_missing_value():
    with pytest.raises(ValueError, match="--state-dir"):
        _extract_stop_state_dir(["--state-dir"])


def test_instance_targets():
    instances = [
        {"instance_id": 1, "state_dir": "/a/ws"},
        {"instance_id": 2, "state_dir": "/b/ws"},
    ]
    assert _instance_targets(instances) == [(1, "/a/ws"), (2, "/b/ws")]


def test_parse_stop_all():
    targets = [(1, "/a"), (3, "/a"), (5, "/b")]
    ids, err = _parse_stop_target_ids(["--all"], targets)
    assert err is None
    assert ids == [(1, "/a"), (3, "/a"), (5, "/b")]


def test_parse_stop_all_lowercase():
    ids, err = _parse_stop_target_ids(["all"], [(2, "/x")])
    assert err is None
    assert ids == [(2, "/x")]


def test_parse_stop_numeric_ids_order():
    targets = [(1, "/a"), (5, "/a"), (7, "/b")]
    ids, err = _parse_stop_target_ids(["5", "1"], targets)
    assert err is None
    assert ids == [(5, "/a"), (1, "/a")]


def test_parse_stop_dedup():
    ids, err = _parse_stop_target_ids(["3", "3", "1"], [(1, "/a"), (3, "/a")])
    assert err is None
    assert ids == [(3, "/a"), (1, "/a")]


def test_parse_stop_unknown_id():
    _, err = _parse_stop_target_ids(["99"], [(1, "/a")])
    assert err is not None
    assert "不在" in err


def test_parse_stop_bad_token():
    _, err = _parse_stop_target_ids(["--feishu"], [(1, "/a")])
    assert err is not None


def test_parse_stop_invalid_number():
    _, err = _parse_stop_target_ids(["x"], [(1, "/a")])
    assert err is not None


def test_parse_stop_ambiguous_id_without_state_dir():
    targets = [(1, "/a/ws"), (1, "/b/ws")]
    _, err = _parse_stop_target_ids(["1"], targets)
    assert err is not None
    assert "多个状态目录" in err


def test_parse_stop_with_state_dir_filter():
    targets = [(1, "/a/ws"), (1, "/b/ws")]
    ids, err = _parse_stop_target_ids(["1"], targets, filter_state_dir="/a/ws")
    assert err is None
    assert ids == [(1, "/a/ws")]


def test_parse_stop_with_state_dir_normcase():
    targets = [(1, r"C:\Users\test\workspaces")]
    ids, err = _parse_stop_target_ids(
        ["1"], targets, filter_state_dir=r"c:\users\test\workspaces"
    )
    assert err is None
    assert ids == [(1, r"C:\Users\test\workspaces")]
