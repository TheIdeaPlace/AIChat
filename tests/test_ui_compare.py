"""Comparing a probe run with the baseline (#155), on made-up pictures."""
import json
import sys
from pathlib import Path

import pytest
import wx

wx = pytest.importorskip("wx")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import ui_compare  # noqa: E402


def _control(cls, label, rect, **extra):
    return {"class": cls, "name": cls.lower(), "label": label, "rect": rect,
            "best": rect[2:], "depth": 0, "enabled": True, "problems": [], **extra}


def _picture(path, box=None, colour=(0, 0, 0)):
    image = wx.Image(80, 60)
    image.SetRGB(wx.Rect(0, 0, 80, 60), 255, 255, 255)
    if box:
        image.SetRGB(wx.Rect(*box), *colour)
    image.SaveFile(str(path), wx.BITMAP_TYPE_PNG)


def _surface(folder, stem, controls, box=None, colour=(0, 0, 0), title="Settings"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.json").write_text(json.dumps(
        {"title": title, "size": [80, 60], "controls": controls}), encoding="utf-8")
    _picture(folder / f"{stem}.png", box, colour)


def test_layout_changes_are_said_in_words():
    old = {"title": "Settings", "controls": [_control("Button", "OK", [10, 10, 80, 24]),
                                             _control("Button", "Cancel", [100, 10, 80, 24])]}
    new = {"title": "Settings", "controls": [
        _control("Button", "OK", [10, 40, 80, 24]),
        _control("Button", "Apply", [100, 10, 80, 24]),
        _control("StaticText", "Voice", [10, 70, 30, 20],
                 problems=["text cut off: smaller than its best size"])]}
    changes = ui_compare.layout_changes(old, new)
    assert "Button 'Cancel' is gone" in changes
    assert "Button 'Apply' is new" in changes
    assert "Button 'OK' moved from (10, 10) to (10, 40)" in changes
    assert not any("StaticText" in c and "now" in c for c in changes)  # it's new, said above


def test_a_pixel_or_two_of_movement_is_not_a_change():
    old = {"controls": [_control("Button", "OK", [10, 10, 80, 24])]}
    new = {"controls": [_control("Button", "OK", [11, 12, 81, 24])]}
    assert ui_compare.layout_changes(old, new) == []


def test_a_new_problem_on_a_control_is_reported():
    old = {"controls": [_control("Button", "OK", [10, 10, 80, 24])]}
    new = {"controls": [_control("Button", "OK", [10, 10, 80, 24],
                                 problems=["text cut off: smaller than its best size"])]}
    assert ui_compare.layout_changes(old, new) == [
        "Button 'OK': now text cut off: smaller than its best size"]


def test_same_pictures_and_layout_are_unchanged(app, tmp_path):
    controls = [_control("Button", "OK", [10, 10, 40, 20])]
    _surface(tmp_path / "base", "settings-light-100", controls, box=(5, 5, 10, 10))
    _surface(tmp_path / "run", "settings-light-100", controls, box=(5, 5, 10, 10))
    result = ui_compare.compare(tmp_path / "run", tmp_path / "base")
    assert result == {"new": [], "missing": [], "unchanged": ["settings-light-100"],
                      "changed": {}, "failed": {}}


def test_a_colour_change_the_layout_cant_see_is_caught(app, tmp_path):
    controls = [_control("Button", "OK", [10, 10, 40, 20])]
    _surface(tmp_path / "base", "settings-dark-100", controls, box=(10, 10, 40, 30))
    _surface(tmp_path / "run", "settings-dark-100", controls, box=(10, 10, 40, 30),
             colour=(255, 0, 255))
    result = ui_compare.compare(tmp_path / "run", tmp_path / "base")
    assert list(result["changed"]) == ["settings-dark-100"]
    # 1200 of the 76 by 56 pixels inside the uncompared border.
    assert result["changed"]["settings-dark-100"] == ["28.2% of the picture's pixels changed"]
    assert (tmp_path / "run" / "diff" / "settings-dark-100.png").exists()


def test_slight_anti_aliasing_differences_are_ignored(app, tmp_path):
    controls = []
    _surface(tmp_path / "base", "usage-light-100", controls, box=(0, 0, 40, 30),
             colour=(100, 100, 100))
    _surface(tmp_path / "run", "usage-light-100", controls, box=(0, 0, 40, 30),
             colour=(110, 108, 104))
    assert ui_compare.compare(tmp_path / "run", tmp_path / "base")["unchanged"] == [
        "usage-light-100"]


def test_new_and_missing_surfaces_and_accepting(app, tmp_path):
    _surface(tmp_path / "base", "old-light-100", [])
    _surface(tmp_path / "run", "fresh-light-100", [])
    _manifest(tmp_path / "run", "light-100", {"fresh": {}})
    result = ui_compare.compare(tmp_path / "run", tmp_path / "base")
    assert result["new"] == ["fresh-light-100"] and result["missing"] == ["old-light-100"]
    assert "## New (no baseline yet)" in ui_compare.report(result)
    assert ui_compare.main([str(tmp_path / "run"), "--baseline", str(tmp_path / "base")]) == 1
    assert (tmp_path / "run" / "compare.md").exists()
    assert ui_compare.main([str(tmp_path / "run"), "--baseline", str(tmp_path / "base"),
                            "--accept", "fresh-light-100"]) == 0
    assert (tmp_path / "base" / "fresh-light-100.png").exists()
    assert not (tmp_path / "base" / "manifest-light-100.json").exists()


def test_a_run_of_one_variant_is_compared_only_with_that_variant(app, tmp_path):
    _surface(tmp_path / "base", "settings-light-100", [])
    _surface(tmp_path / "base", "settings-dark-100", [])
    _surface(tmp_path / "run", "settings-light-100", [])
    _manifest(tmp_path / "run", "light-100", {"settings": {}})
    # A baseline without manifests still keeps to the run's variants.
    result = ui_compare.compare(tmp_path / "run", tmp_path / "base")
    assert result["missing"] == [] and result["unchanged"] == ["settings-light-100"]


def test_accepting_a_whole_run_keeps_its_manifests(app, tmp_path):
    _surface(tmp_path / "run", "usage-dark-100", [])
    _manifest(tmp_path / "run", "dark-100", {"usage": {}})
    assert ui_compare.main([str(tmp_path / "run"), "--baseline", str(tmp_path / "base"),
                            "--accept"]) == 0
    assert (tmp_path / "base" / "manifest-dark-100.json").exists()
    assert (tmp_path / "base" / "usage-dark-100.png").exists()


def test_the_window_border_is_not_compared(app, tmp_path):
    # Windows draws the outer border over whatever is behind the window.
    _surface(tmp_path / "base", "usage-light-150", [], box=(0, 0, 80, 1))
    _surface(tmp_path / "run", "usage-light-150", [], box=(0, 0, 80, 1), colour=(255, 0, 0))
    assert ui_compare.compare(tmp_path / "run", tmp_path / "base")["unchanged"] == [
        "usage-light-150"]


def test_a_screen_the_probe_reported_failed_is_not_accepted(app, tmp_path):
    _surface(tmp_path / "run", "user-guide-light-150", [])
    _surface(tmp_path / "run", "usage-light-150", [])
    (tmp_path / "run" / "manifest-light-150.json").write_text(json.dumps({
        "tag": "light-150", "surfaces": {
            "user-guide": {"error": "the web page never drew: photographed blank"},
            "usage": {}}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="user-guide-light-150: the web page never drew"):
        ui_compare.main([str(tmp_path / "run"), "--baseline", str(tmp_path / "base"),
                         "--accept"])
    assert not (tmp_path / "base").exists()
    assert ui_compare.main([str(tmp_path / "run"), "--baseline", str(tmp_path / "base"),
                            "--accept", "usage-light-150"]) == 0


def test_an_added_control_sharing_a_key_with_others_is_reported():
    # Three unlabelled texts where there were two: same class, name and label.
    old = {"controls": [_control("StaticText", "", [0, 0, 10, 10]),
                        _control("StaticText", "", [0, 20, 10, 10])]}
    new = {"controls": old["controls"] + [_control("StaticText", "", [0, 40, 10, 10],
                                                   problems=["text cut off"])]}
    assert ui_compare.layout_changes(old, new) == ["StaticText 'statictext' is new (text cut off)"]
    assert ui_compare.layout_changes(new, old) == ["StaticText 'statictext' is gone"]


def test_state_changes_are_reported_and_text_is_summed_up():
    a = _control("Button", "OK", [0, 0, 80, 24])
    b = dict(a, enabled=False)
    assert ui_compare.layout_changes({"controls": [a]}, {"controls": [b]}) == [
        "Button 'OK' is now disabled"]
    box = _control("TextCtrl", "x" * 500, [0, 0, 200, 100], value="x" * 500)
    edited = dict(box, label="y" * 500, value="y" * 500)
    changes = ui_compare.layout_changes({"controls": [box]}, {"controls": [edited]})
    assert changes == ["TextCtrl 'textctrl''s text changed"]
    picked = _control("ListBox", "", [0, 0, 100, 100], items=["a", "b"], selection=0)
    moved = dict(picked, selection=1)
    assert ui_compare.layout_changes({"controls": [picked]}, {"controls": [moved]}) == [
        "ListBox 'listbox''s selection moved from 0 to 1"]


def test_a_long_label_is_shortened_in_the_report():
    old = {"controls": [_control("StaticText", "A" * 300, [0, 0, 10, 10])]}
    (line,) = ui_compare.layout_changes(old, {"controls": []})
    assert len(line) < 90 and line.endswith("...' is gone")


def _manifest(folder, tag, surfaces):
    (folder / (f"manifest-{tag}.json" if tag else "manifest.json")).write_text(
        json.dumps({"tag": tag, "surfaces": surfaces}), encoding="utf-8")


def test_a_screen_the_probe_reported_failed_is_listed_not_compared(app, tmp_path):
    _surface(tmp_path / "base", "user-guide-light-150", [])
    _manifest(tmp_path / "base", "light-150", {"user-guide": {}})
    _surface(tmp_path / "run", "user-guide-light-150", [], box=(5, 5, 50, 40))
    _manifest(tmp_path / "run", "light-150",
              {"user-guide": {"error": "the web page never drew: photographed blank"}})
    result = ui_compare.compare(tmp_path / "run", tmp_path / "base")
    assert result["failed"] == {"user-guide-light-150": "the web page never drew: "
                                                        "photographed blank"}
    assert result["changed"] == {} and result["unchanged"] == []
    assert "## Failed in this run" in ui_compare.report(result)
    assert ui_compare.main([str(tmp_path / "run"), "--baseline", str(tmp_path / "base")]) == 1


def test_variants_are_matched_by_their_tags_not_by_how_names_end(app, tmp_path):
    # A baseline variant "hc-light-100" ends like "light-100"; a light-100 run
    # isn't missing its screens.
    _surface(tmp_path / "base", "settings-light-100", [])
    _surface(tmp_path / "base", "settings-hc-light-100", [])
    _manifest(tmp_path / "base", "light-100", {"settings": {}})
    _manifest(tmp_path / "base", "hc-light-100", {"settings": {}})
    _surface(tmp_path / "run", "settings-light-100", [])
    _manifest(tmp_path / "run", "light-100", {"settings": {}})
    result = ui_compare.compare(tmp_path / "run", tmp_path / "base")
    assert result["missing"] == [] and result["unchanged"] == ["settings-light-100"]


def test_accepting_a_name_not_in_the_run_changes_nothing(app, tmp_path):
    _surface(tmp_path / "run", "usage-light-100", [])
    with pytest.raises(SystemExit, match="not in the run"):
        ui_compare.accept(tmp_path / "run", tmp_path / "base",
                          ["usage-light-100", "typo-light-100"])
    assert not (tmp_path / "base").exists()


def test_failures_in_a_run_without_a_tag_are_refused(app, tmp_path):
    _surface(tmp_path / "run", "settings", [])
    _manifest(tmp_path / "run", "", {"settings": {"error": "the web page never drew"}})
    with pytest.raises(SystemExit, match="settings: the web page never drew"):
        ui_compare.accept(tmp_path / "run", tmp_path / "base", [])


def test_a_partial_run_is_compared_only_with_the_surfaces_it_took(app, tmp_path):
    for name in ("settings", "usage"):
        _surface(tmp_path / "base", f"{name}-light-100", [])
    _manifest(tmp_path / "base", "light-100", {"settings": {}, "usage": {}})
    _surface(tmp_path / "run", "settings-light-100", [])
    (tmp_path / "run" / "manifest-light-100.json").write_text(json.dumps(
        {"tag": "light-100", "complete": False, "surfaces": {"settings": {}}}), encoding="utf-8")
    result = ui_compare.compare(tmp_path / "run", tmp_path / "base")
    assert result["missing"] == [] and result["unchanged"] == ["settings-light-100"]
    # A complete run without usage is missing it.
    (tmp_path / "run" / "manifest-light-100.json").write_text(json.dumps(
        {"tag": "light-100", "complete": True, "surfaces": {"settings": {}}}), encoding="utf-8")
    assert ui_compare.compare(tmp_path / "run", tmp_path / "base")["missing"] == ["usage-light-100"]



def test_accepting_part_of_a_run_keeps_the_baselines_other_screens(tmp_path):
    """Accepting a run of some surfaces (-Surface) once replaced the
    baseline's manifest with the run's, dropping every other screen."""
    import json
    baseline, run = tmp_path / "baseline", tmp_path / "run"
    baseline.mkdir()
    run.mkdir()
    full = {"tag": "light-100", "os": "old", "surfaces": {"a": {"png": "a.png"},
                                                           "b": {"png": "b.png"}}}
    (baseline / "manifest-light-100.json").write_text(json.dumps(full), encoding="utf-8")
    part = {"tag": "light-100", "os": "new", "complete": False,
            "surfaces": {"b": {"png": "b2.png"}, "c": {"png": "c.png"}}}
    (run / "manifest-light-100.json").write_text(json.dumps(part), encoding="utf-8")
    ui_compare._merge_manifest(run / "manifest-light-100.json",
                               baseline / "manifest-light-100.json")
    merged = json.loads((baseline / "manifest-light-100.json").read_text(encoding="utf-8"))
    assert merged["surfaces"] == {"a": {"png": "a.png"}, "b": {"png": "b2.png"},
                                  "c": {"png": "c.png"}}
    assert merged["os"] == "old" and "complete" not in merged
    whole = {"tag": "light-100", "os": "newer", "surfaces": {"a": {"png": "a3.png"}}}
    (run / "manifest-light-100.json").write_text(json.dumps(whole), encoding="utf-8")
    ui_compare._merge_manifest(run / "manifest-light-100.json",
                               baseline / "manifest-light-100.json")
    merged = json.loads((baseline / "manifest-light-100.json").read_text(encoding="utf-8"))
    # A complete run is the probe's whole list now: screens gone from it go.
    assert merged["os"] == "newer" and set(merged["surfaces"]) == {"a"}


def test_accepting_by_name_adds_those_screens_to_the_manifest(app, tmp_path):
    import json
    baseline, run = tmp_path / "baseline", tmp_path / "run"
    for folder in (baseline, run):
        folder.mkdir()
    (baseline / "manifest-light-100.json").write_text(json.dumps(
        {"tag": "light-100", "surfaces": {"a": {"png": "a-light-100.png"}}}), encoding="utf-8")
    (run / "manifest-light-100.json").write_text(json.dumps(
        {"tag": "light-100", "complete": False,
         "surfaces": {"b": {"png": "b-light-100.png"}, "c": {"png": "c-light-100.png"}}}),
        encoding="utf-8")
    for stem in ("b-light-100", "c-light-100"):
        wx.Bitmap(4, 4).SaveFile(str(run / f"{stem}.png"), wx.BITMAP_TYPE_PNG)
        (run / f"{stem}.json").write_text("{}", encoding="utf-8")
    ui_compare.accept(run, baseline, ["b-light-100"])
    merged = json.loads((baseline / "manifest-light-100.json").read_text(encoding="utf-8"))
    assert set(merged["surfaces"]) == {"a", "b"} and "complete" not in merged
