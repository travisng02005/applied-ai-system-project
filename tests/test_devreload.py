import importlib
import os
import sys

import devreload


def write(path, text, mtime):
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_edited_modules_are_reloaded_and_dependents_follow(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(devreload, "_seen", {})
    write(tmp_path / "dr_base.py", "VALUE = 1\n", 1_000)
    write(tmp_path / "dr_user.py", "import dr_base\nSEEN = dr_base.VALUE\n", 1_000)
    base, user = importlib.import_module("dr_base"), importlib.import_module("dr_user")
    try:
        names = ["dr_base", "dr_user"]
        assert devreload.reload_if_changed(names) == []  # first sight only records
        assert devreload.reload_if_changed(names) == []  # nothing changed

        write(tmp_path / "dr_base.py", "VALUE = 2\n", 2_000)
        assert devreload.reload_if_changed(names) == ["dr_base", "dr_user"]
        assert base.VALUE == 2 and user.SEEN == 2  # dependent re-read the new value
        assert devreload.reload_if_changed(names) == []  # settled again
    finally:
        sys.modules.pop("dr_base", None)
        sys.modules.pop("dr_user", None)


def test_unimported_modules_are_ignored():
    assert devreload.reload_if_changed(["definitely_not_imported_xyz"]) == []
