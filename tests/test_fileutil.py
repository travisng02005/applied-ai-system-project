import logging

import pytest

import config
from fileutil import atomic_write_text, move_aside_corrupt


def test_atomic_write_creates_folders_replaces_content_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "deep" / "folder" / "data.json"

    atomic_write_text(target, "first")
    atomic_write_text(target, "second")

    assert target.read_text(encoding="utf-8") == "second"
    assert [p.name for p in target.parent.iterdir()] == ["data.json"]  # no .tmp leftovers


def test_atomic_write_raises_oserror_when_the_folder_is_impossible(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OSError):
        atomic_write_text(blocker / "data.json", "text")


def test_move_aside_renames_and_keeps_the_content(tmp_path):
    bad = tmp_path / "data.json"
    bad.write_text("garbage", encoding="utf-8")

    backup = move_aside_corrupt(bad)

    assert not bad.exists()
    assert backup.name.startswith("data.json.corrupt-")
    assert backup.read_text(encoding="utf-8") == "garbage"


def test_move_aside_of_a_missing_file_returns_the_original_path(tmp_path):
    missing = tmp_path / "nope.json"
    assert move_aside_corrupt(missing) == missing


def test_configure_logging_is_idempotent(tmp_path, monkeypatch):
    root = logging.getLogger("pawpal")
    before = list(root.handlers)
    try:
        config.configure_logging(str(tmp_path / "x.log"))
        config.configure_logging(str(tmp_path / "x.log"))  # a second call must not add more
        config.configure_logging(str(tmp_path / "x.log"))
        added = [h for h in root.handlers if h not in before]
        assert len(added) <= 2  # at most one text handler and one JSON handler in total
    finally:
        for handler in [h for h in root.handlers if h not in before]:
            root.removeHandler(handler)
            handler.close()
