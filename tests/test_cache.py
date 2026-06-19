from __future__ import annotations

from generate_imports.cache import ResolveCache


def test_set_get_roundtrip(tmp_path):
    c = ResolveCache(str(tmp_path / "c.json"))
    c.set("az role assignment list ...", "/result/id")
    assert c.get("az role assignment list ...") == "/result/id"
    assert c.get("missing") is None


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "c.json")
    c1 = ResolveCache(path)
    c1.set("k", "v")
    c1.save()

    c2 = ResolveCache(path)
    assert c2.get("k") == "v"
    assert c2.hits == 1


def test_does_not_store_empty(tmp_path):
    c = ResolveCache(str(tmp_path / "c.json"))
    c.set("k", "")
    assert c.get("k") is None


def test_disabled_cache_is_noop(tmp_path):
    c = ResolveCache(str(tmp_path / "c.json"), enabled=False)
    c.set("k", "v")
    c.save()
    assert c.get("k") is None
    assert not (tmp_path / "c.json").exists()


def test_none_path_disables(tmp_path):
    c = ResolveCache(None)
    assert c.enabled is False
    c.set("k", "v")
    assert c.get("k") is None


def test_save_only_writes_when_dirty(tmp_path):
    path = tmp_path / "c.json"
    c = ResolveCache(str(path))
    c.save()  # nothing set -> not dirty
    assert not path.exists()
    c.set("k", "v")
    c.save()
    assert path.exists()


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("not json{", encoding="utf-8")
    c = ResolveCache(str(path))
    assert c.get("k") is None  # loaded as empty, no crash
