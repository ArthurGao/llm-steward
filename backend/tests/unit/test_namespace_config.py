"""The namespace config model (design §8): validated on write, defaults for what is not set."""

import pytest

from llm_steward.errors import Unprocessable
from llm_steward.namespace_config import InvalidStoredConfig, NamespaceConfig, load


def test_an_empty_config_is_every_default() -> None:
    config = NamespaceConfig.parse({})
    assert config.kb.suggest_threshold == 0.7
    assert config.memory.pii.action == "redact"
    assert (config.retrieval.top_k, config.limits.requests_per_minute) == (None, None)
    assert config.webhooks == [] and config.documents.url_allowlist == []
    assert NamespaceConfig.parse(None) == config


def test_a_section_written_empty_is_the_default() -> None:
    """`kb:` with nothing under it is null in YAML/JSON."""
    config = NamespaceConfig.parse({"kb": None, "memory": {"pii": None, "types": None}})
    assert config == NamespaceConfig.parse({})


def test_unknown_keys_are_refused_and_named() -> None:
    with pytest.raises(Unprocessable) as exc:
        NamespaceConfig.parse({"kb": {"suggest_treshold": 0.5}, "providers": {"kb": {}}})
    assert "kb.suggest_treshold" in exc.value.detail
    assert "providers" in exc.value.detail


@pytest.mark.parametrize(
    "raw",
    [
        {"kb": {"suggest_threshold": 1.5}},
        {"retrieval": {"top_k": 0}},
        {"retrieval": {"top_k": "many"}},
        {"retrieval": {"trust_weights": {"draft": 1.0}}},
        {"memory": {"pii": {"action": "shred"}}},
        {"memory": {"types": [{"name": "order_event", "half_life_days": 0}]}},
        {"limits": {"requests_per_minute": 0}},
        {"webhooks": [{"url": "https://h.example/x", "events": [], "secret_env": "STEWARD_X"}]},
        {"webhooks": [{"url": "https://h", "events": ["kb_entry.verified"], "secret_env": "PATH"}]},
        {"webhooks": [{"url": "https://h", "events": ["memory.read"], "secret_env": "STEWARD_X"}]},
    ],
)
def test_bad_values_are_refused(raw: dict[str, object]) -> None:
    with pytest.raises(Unprocessable):
        NamespaceConfig.parse(raw)


def test_only_what_was_set_is_stored() -> None:
    """Later changes to a default still reach a namespace that never set it."""
    stored = NamespaceConfig.parse({"retrieval": {"top_k": 2}, "kb": None}).dump()
    assert stored == {"retrieval": {"top_k": 2}, "kb": {}}


def test_a_stored_config_that_no_longer_validates_says_how_to_fix_it() -> None:
    with pytest.raises(InvalidStoredConfig) as exc:
        load({"retrieval": {"top_k": -1}})
    assert "retrieval.top_k" in exc.value.detail and "steward ns apply" in exc.value.detail
