import pytest
from gis_assistant_ai.integration_validation import checked_plan
from gis_assistant_ai.utils import extract_json


def test_truncated_plan_does_not_become_nested_answer():
    with pytest.raises(ValueError):
        extract_json('{"type":"plan","steps":[{"id":"s1","action":"note"}')


@pytest.mark.parametrize("steps", [
    [], [{"id": "s1"}, {"id": "s1"}], [{"id": "bad.id"}],
    [{"id": "s1", "layer": "{{missing.OUTPUT}}"}],
    [{"id": "s1", "layer": "{{s2.OUTPUT}}"}, {"id": "s2"}],
    [{"action": "unknown"}], [{"action": "processing"}],
    [{"action": "processing", "algorithm": "native:buffer", "params": []}],
    [{"action": "python", "code": None}], [{"action": "load_layer"}], ["not an object"],
])
def test_invalid_plan_rejected(steps):
    with pytest.raises(ValueError):
        checked_plan({"type": "plan", "steps": steps})


def test_valid_dependencies_and_plugin_owned_state():
    plan = checked_plan({"type": "plan", "steps": [
        {"id": "a", "action": "user_action", "_status": "done"},
        {"id": "b", "action": "processing", "algorithm": "native:buffer", "params": {"INPUT": "{{a.OUTPUT}}"}},
    ]})
    assert plan["steps"][0]["_status"] == "pending"
    assert plan["steps"][1]["params"]["INPUT"] == "{{a.OUTPUT}}"


def test_existing_action_and_algorithm_aliases_are_preserved():
    plan = checked_plan({"type": "plan", "steps": [
        {"id": "s1", "action": " Algorithm ", "algorithm_id": "native:buffer"}]})
    assert plan["steps"][0]["algorithm"] == "native:buffer"
    assert plan["steps"][0]["action"] == "processing"
