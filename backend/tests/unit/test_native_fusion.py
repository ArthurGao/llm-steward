"""Fusion inside the native provider: a lexical hit on a generic word must not outrank the vector's
clear winner (found by the real-model test: '用户' / 'the user' matched the wrong memory)."""

from llm_steward.providers.native.providers import lexical_matches, rrf
from llm_steward.providers.native.store import StoredItem
from llm_steward.providers.native.text import tokens


def _item(eid: str, text: str) -> StoredItem:
    return StoredItem(eid, text, tokens(text), None, None, None, {})


COFFEE = _item("coffee", "The user drinks black coffee every morning")
CITY = _item("city", "用户住在上海")
ORDER = _item("order", "Order 123 asked for a refund")


def test_hits_sharing_only_a_generic_word_are_dropped() -> None:
    assert lexical_matches(tokens("用户早上喝什么"), [CITY]) == []
    assert lexical_matches(tokens("Which city does the user live in?"), [COFFEE]) == []


def test_hits_covering_the_query_are_kept() -> None:
    assert lexical_matches(tokens("order 123 refund"), [ORDER]) == [ORDER]


def test_vector_winner_stays_first_when_lexical_noise_is_filtered() -> None:
    by_vector = [COFFEE, CITY]
    by_text = lexical_matches(tokens("用户早上喝什么"), [CITY])
    assert [h.external_id for h in rrf([by_vector, by_text], top_k=2)] == ["coffee", "city"]
