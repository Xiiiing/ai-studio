"""Material management contracts using isolated media and SQLite."""
import base64
import copy
import io

import pytest
from PIL import Image

from factory import store, material_library as library
from studio import core


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(store, "DATA", tmp_path / "runtime")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "runtime/studio.sqlite3")
    store.initialize()
    return store.create({"brief": "一个成年人在车站重逢的原创故事", "series_key": "shared-series"})


def picture(size=(128, 128)):
    output = io.BytesIO()
    image = Image.new("RGB", size)
    image.putdata([((x*17)%256, (x*29)%256, (x*43)%256) for x in range(size[0]*size[1])])
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode()


def material(project=None, kind="character", name="林夏", approve=False):
    value = library.mutate({"action": "create", "kind": kind, "scope": "project" if project else "global",
                            "project_id": project["id"] if project else None,
                            "document": {"name": name, "description": "成年短发人物"}})
    value = library.mutate({"action": "upload", "asset_id": value["asset_id"], "expected_version": value["version"],
                            "project_id": project["id"] if project else None, "image_base64": picture(), "label": "正面"})
    if approve:
        value = library.mutate({"action": "approve", "asset_id": value["asset_id"], "expected_version": value["version"],
                               "project_id": project["id"] if project else None, "confirmed": True})
    return value


def select(project, value, role="female", **fields):
    return library.mutate_project(project["id"], {"action": "select", "expected_revision": store.get(project["id"])["revision"],
        "references": [{"asset_id": value["asset_id"], "version": value["version"], "role": role, **fields}]})


def confirm(project):
    return library.mutate_project(project["id"], {"action": "confirm", "confirmed": True, "expected_revision": store.get(project["id"])["revision"]})


def test_global_and_drama_libraries_are_independent_and_searchable(project):
    global_asset = material()
    local = material(project, name="本剧林夏")
    assert {a["asset_id"] for a in library.inventory()["assets"]} == {global_asset["asset_id"]}
    assert len(library.inventory(project["id"])["assets"]) == 2
    assert library.inventory(project["id"], "本剧")["assets"][0]["asset_id"] == local["asset_id"]
    other = store.create({"brief": "另一个独立的成年人物故事"})
    with store.connection() as db, pytest.raises(ValueError, match="私有"):
        library.accessible(library.get(db, local["asset_id"]), other)


def test_uploaded_and_approved_versions_never_overwrite_old_files(project):
    first = material(approve=True)
    original = library.image_path(first["data"]["images"][0]).read_bytes()
    latest = library.mutate({"action": "upload", "asset_id": first["asset_id"], "expected_version": first["version"], "image_base64": picture(), "label": "侧面"})
    assert latest["status"] == "candidate" and len(latest["data"]["images"]) == 2
    with store.connection() as db:
        assert library.get(db, first["asset_id"], first["version"])["status"] == "approved"
    assert library.image_path(first["data"]["images"][0]).read_bytes() == original
    with pytest.raises(ValueError, match="已更新"):
        library.mutate({"action": "revise", "asset_id": first["asset_id"], "expected_version": first["version"], "document": {"name": "旧草稿"}})


def test_project_edit_of_global_asset_requires_independent_copy(project):
    value = material()
    with pytest.raises(ValueError, match="剧内造型"):
        library.mutate({"action": "revise", "project_id": project["id"], "asset_id": value["asset_id"], "expected_version": value["version"], "document": {"name": "修改"}})
    cloned = library.mutate({"action": "clone", "project_id": project["id"], "asset_id": value["asset_id"], "expected_version": value["version"], "scope": "project"})
    assert cloned["data"]["source"] == {"asset_id": value["asset_id"], "version": value["version"]}
    assert cloned["library_key"] == library.library_key(project)


@pytest.mark.parametrize("encoded", ["invalid!", "", picture((32, 32)), base64.b64encode(b"not an image").decode()])
def test_upload_rejects_invalid_images(project, encoded):
    value = material()
    with pytest.raises((ValueError, OSError)):
        library.mutate({"action": "upload", "asset_id": value["asset_id"], "expected_version": value["version"], "image_base64": encoded})


def test_confirm_pins_versions_and_new_episodes_inherit_without_mutating_existing(project):
    value = material(project)
    selected = select(project, value)
    assert not selected.get("material_package")
    pinned = confirm(project)
    second = store.create({"brief": "同一个系列下一集的成年人物故事", "series_key": project["series_key"]})
    assert second["material_package"] == pinned["material_package"]
    latest = library.mutate({"action": "revise", "project_id": project["id"], "asset_id": value["asset_id"], "expected_version": value["version"], "document": {"name": "新版造型"}})
    select(project, latest)
    assert store.get(project["id"])["material_package"]["references"][0]["version"] == value["version"]
    assert store.get(second["id"])["material_package"] == second["material_package"]


def test_composition_requires_distinct_stable_roles_and_valid_regions(project):
    character = material()
    member = {"asset_id": character["asset_id"], "version": character["version"], "role": "female", "box": [0, 0, .5, 1]}
    value = library.mutate({"action": "create", "kind": "composition", "document": {"name": "双人站台", "members": [member]}})
    assert value["data"]["document"]["members"][0] == member
    with pytest.raises(ValueError, match="不能重复"):
        library.validate_document({"name": "重复", "members": [member, member]}, "composition")
    with pytest.raises(ValueError, match="坐标"):
        library.validate_document({"name": "越界", "members": [{**member, "box": [0, 0, 2, 1]}]}, "composition")


def test_selection_fences_revision_and_validates_role(project):
    value = material()
    with pytest.raises(ValueError, match="稳定角色"):
        select(project, value, "not_a_role")
    select(project, value)
    with pytest.raises(ValueError, match="项目已更新"):
        library.mutate_project(project["id"], {"action": "confirm", "expected_revision": project["revision"], "confirmed": True})


def test_character_reference_tracks_visible_silent_character(project):
    value = material()
    select(project, value); project = confirm(project)
    project["episode"] = {"cast": {"female": "林夏", "male": "张明"}}
    assert library.shot_references(project, {"id": "S01", "characters": ["female"], "dialogue": []})
    assert not library.shot_references(project, {"id": "S02", "characters": ["male"], "dialogue": []})


def test_tampered_reference_cannot_be_confirmed(project):
    value = material()
    select(project, value)
    library.image_path(value["data"]["images"][0]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="校验失败"):
        confirm(project)


def test_primary_view_can_change_without_replacing_old_version(project):
    value = material(approve=True)
    second = library.mutate({"action": "upload", "asset_id": value["asset_id"], "expected_version": value["version"], "image_base64": picture((256, 256)), "label": "新版主参考"})
    primary = library.mutate({"action": "set_primary", "asset_id": value["asset_id"], "expected_version": second["version"], "image_index": 1})
    assert primary["data"]["images"][0]["label"] == "新版主参考"
    with store.connection() as db:
        original = library.get(db, value["asset_id"], value["version"])
        assert original["data"]["images"][0]["label"] == "正面" and original["status"] == "approved"


def test_new_episode_inherits_materials_but_directs_its_own_scene_placements(project):
    scene = material(project, kind="scene", name="站台")
    select(project, scene, scene["asset_id"], shot_ids=["S01"])
    first = confirm(project)
    second = store.create({"brief": "同系列下一集的成年人物故事", "series_key": project["series_key"]})
    assert first["material_package"]["references"][0]["shot_ids"] == ["S01"]
    assert "shot_ids" not in second["material_package"]["references"][0]


def test_selection_snapshot_exposes_canonical_characters_for_episode_owned_profile(project):
    from factory.production_profiles import normalize
    profile = normalize({"preset": "compact6"})
    store.update(project["id"], {"production_profile": None, "duration_seconds": 30,
                               "episode": {"production_profile": profile}})
    before = store.get(project["id"])
    result = library.snapshot(project["id"])
    assert result["characters"] == profile["characters"]
    assert result["revision"] == before["revision"]
    assert store.get(project["id"]) == before
