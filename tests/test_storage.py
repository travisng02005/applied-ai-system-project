import json
from datetime import datetime, time

import pytest

from pawpal_system import Category, Frequency, Owner, Pet, Priority, Task
from storage import StorageError, load_owner, owner_from_dict, owner_to_dict, save_owner


def sample_owner():
    owner = Owner("Travis", available_minutes_per_day=90,
                  preferences={"day_start": time(7, 30), "max_delay_minutes": 45})
    mia = Pet("Mia", "cat", age=5, special_needs="thyroid meds")
    owner.add_pet(mia)
    owner.add_pet(Pet("Rex", "dog"))
    mia.add_task(Task("Pill", Category.MEDS, 5, Priority.HIGH, datetime(2026, 10, 20, 8, 15),
                      Frequency.DAILY))
    done = Task("Brush", Category.GROOMING, 20, Priority.LOW)
    mia.add_task(done)
    done.mark_complete()
    return owner


def test_round_trip_keeps_everything(tmp_path):
    path = tmp_path / "data.json"
    assert save_owner(sample_owner(), path)

    loaded = load_owner(path)

    assert owner_to_dict(loaded) == owner_to_dict(sample_owner())
    assert loaded.preferences["day_start"] == time(7, 30)
    mia = loaded.pets[0]
    assert mia.tasks[0].pet is mia  # back-references are rebuilt
    assert mia.tasks[0].category is Category.MEDS
    assert mia.tasks[1].completed is True


def test_file_is_readable_json(tmp_path):
    path = tmp_path / "data.json"
    save_owner(sample_owner(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["owner"]["name"] == "Travis"
    assert [p["name"] for p in data["pets"]] == ["Mia", "Rex"]


def test_missing_file_means_fresh_start(tmp_path):
    assert load_owner(tmp_path / "nope.json") is None


def test_corrupt_file_is_moved_aside_not_overwritten(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(StorageError):
        load_owner(path)

    assert not path.exists()
    backups = list(tmp_path.glob("data.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ this is not json"


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(version=99),
    lambda d: d["pets"][0]["tasks"][0].update(category="nonsense"),
    lambda d: d["pets"][0]["tasks"][0].update(duration_minutes=-5),
    lambda d: d["owner"].pop("name"),
])
def test_invalid_data_raises_storage_error(mutate):
    data = owner_to_dict(sample_owner())
    mutate(data)
    with pytest.raises(StorageError):
        owner_from_dict(data)


def test_failed_save_returns_false_instead_of_raising(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert save_owner(sample_owner(), blocker / "data.json") is False


def test_shared_tasks_survive_a_save_and_load(tmp_path):
    owner = sample_owner()
    rex = owner.pets[1]
    owner.add_shared_task([owner.pets[0], rex], Task("Vet", Category.VET, 30))
    path = tmp_path / "data.json"

    save_owner(owner, path)
    loaded = load_owner(path)

    vet = [t for t in loaded.get_all_tasks() if t.title == "Vet"]
    assert len(vet) == 1  # still one task
    assert vet[0].pet is loaded.pets[0] and vet[0].shared_with == [loaded.pets[1]]


def test_old_files_without_shared_with_still_load():
    data = owner_to_dict(sample_owner())
    for pet in data["pets"]:
        for task in pet["tasks"]:
            del task["shared_with"]
    assert owner_from_dict(data).pets[0].tasks[0].shared_with == []


def test_available_from_survives_a_save_and_load(tmp_path):
    from datetime import date
    owner = sample_owner()
    owner.pets[0].add_task(Task("Brush again", frequency=Frequency.DAILY, available_from=date(2026, 10, 21)))
    path = tmp_path / "data.json"
    save_owner(owner, path)
    assert load_owner(path).pets[0].tasks[-1].available_from == date(2026, 10, 21)
