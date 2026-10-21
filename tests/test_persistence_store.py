from __future__ import annotations

from pathlib import Path

from figai.persistence.store import JsonStore


def test_load_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "state.json")
    assert store.load() == {}


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "state.json")
    data = {"a": 1, "b": [1, 2, 3]}

    store.save(data)

    assert store.load() == data


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "nested" / "dir" / "state.json")
    store.save({"x": 1})
    assert store.load() == {"x": 1}


def test_load_returns_empty_dict_for_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    store = JsonStore(path)

    assert store.load() == {}


def test_save_overwrites_previous_contents(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "state.json")
    store.save({"a": 1})
    store.save({"b": 2})

    assert store.load() == {"b": 2}


def test_save_does_not_leave_temp_files_behind(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "state.json")
    store.save({"a": 1})

    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-state-")]
    assert leftovers == []
