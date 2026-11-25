import pytest

from app.infrastructure.execution.magic_rename import MagicRename


@pytest.mark.parametrize("custom", [False, True])
def test_task_names_and_mutable_variables_are_instance_local(custom):
    overrides = {"{EXT}": [r"\w+$"]} if custom else {}
    a = MagicRename(magic_variable=overrides)
    b = MagicRename(magic_variable=overrides)
    a.set_taskname("Alpha")
    b.set_taskname("Beta")
    pattern = r"^.+$"
    template = "{TASKNAME}.{EXT}"
    assert a.sub(pattern, template, "01.mkv") == "Alpha.mkv"
    assert b.sub(pattern, template, "02.mp4") == "Beta.mp4"
    assert a.sub(pattern, template, "03.mov") == "Alpha.mov"
    a.magic_variable["{EXT}"].append(r"^\w+")
    assert len(b.magic_variable["{EXT}"]) == 1
    if custom:
        assert len(overrides["{EXT}"]) == 1


def test_mutating_a_rename_rule_does_not_change_other_instances():
    rules = {"$CUSTOM": {"pattern": r"^source", "replace": "Original"}}
    first = MagicRename(magic_regex=rules)
    second = MagicRename(magic_regex=rules)
    first.magic_regex["$CUSTOM"]["replace"] = "Changed"
    assert first.magic_regex_conv("$CUSTOM", "") == (r"^source", "Changed")
    assert second.magic_regex_conv("$CUSTOM", "") == (r"^source", "Original")
    assert rules["$CUSTOM"]["replace"] == "Original"
