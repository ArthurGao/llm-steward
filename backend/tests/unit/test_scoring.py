"""Retrieval scoring (design §6 steps 6, 7 and the token budget): pure functions, hand-computed."""

import math

import pytest

from llm_steward.ranking import estimate_tokens, fit_budget, mmr, similarity_terms, weigh


def test_weigh_multiplies_trust_importance_and_decay() -> None:
    # rel * w_trust * (1 + 0.3 · importance) * 0.5^(age / h)
    assert weigh(0.02, trust=0.9, importance=0.5, age_days=0, half_life_days=None) == (
        pytest.approx(0.02 * 0.9 * 1.15)
    )
    assert weigh(0.02, trust=1.0, importance=1.0, age_days=0, half_life_days=None) == (
        pytest.approx(0.02 * 1.3)
    )


def test_decay_halves_the_score_every_half_life() -> None:
    fresh = weigh(1.0, trust=1.0, importance=0.0, age_days=0, half_life_days=30)
    one = weigh(1.0, trust=1.0, importance=0.0, age_days=30, half_life_days=30)
    two = weigh(1.0, trust=1.0, importance=0.0, age_days=60, half_life_days=30)
    assert (fresh, one, two) == pytest.approx((1.0, 0.5, 0.25))
    # no half-life: never decays, however old
    assert weigh(1.0, trust=1.0, importance=0.0, age_days=3650, half_life_days=None) == 1.0
    # the boost is configurable (namespace config)
    assert weigh(1.0, trust=1.0, importance=1.0, age_days=0, half_life_days=None, boost=0.0) == 1.0


def test_a_future_timestamp_never_boosts() -> None:
    assert weigh(1.0, trust=1.0, importance=0.0, age_days=-5, half_life_days=30) == 1.0


def test_similarity_terms_split_words_and_chinese_bigrams() -> None:
    assert similarity_terms("Refunds take 3-5 days") == {"refunds", "take", "3", "5", "days"}
    assert similarity_terms("退款七天") == {"退款", "款七", "七天"}
    assert similarity_terms("退") == {"退"}


def test_mmr_drops_a_near_duplicate_for_something_different() -> None:
    items = [
        ("a", 1.0, similarity_terms("refunds take seven days after the return")),
        ("a-copy", 0.98, similarity_terms("refunds take seven days after the return arrives")),
        ("b", 0.7, similarity_terms("shipping is free above fifty euros")),
    ]
    # step 2 at λ = 0.7: the copy 0.7·0.98 - 0.3·(7/8) = 0.42; b 0.7·0.7 - 0 = 0.49
    assert mmr(items, k=2, lam=0.7) == ["a", "b"]  # the copy adds nothing new
    assert mmr(items, k=3, lam=0.7) == ["a", "b", "a-copy"]
    assert mmr(items, k=2, lam=1.0) == ["a", "a-copy"]  # λ = 1: relevance only


def test_an_item_refused_by_accept_neither_counts_nor_crowds_out_its_neighbours() -> None:
    items = [
        ("long", 1.0, {"refund", "detail"}),
        ("first", 0.9, {"refund", "approved"}),
        ("other", 0.8, {"store", "credit"}),
    ]
    # without "long" picked, "first" is not penalised for sharing "refund" with it
    assert mmr(items, k=2, accept=lambda key: key != "long") == ["first", "other"]
    # picked, "long" crowds "first" out: 0.7 * 0.9 - 0.3 * (1/3) = 0.53 < other's 0.56
    assert mmr(items, k=2) == ["long", "other"]


def test_mmr_keeps_order_when_nothing_overlaps() -> None:
    items = [(str(i), 1.0 / (i + 1), {f"t{i}"}) for i in range(5)]
    assert mmr(items, k=5, lam=0.7) == ["0", "1", "2", "3", "4"]
    assert mmr([], k=3, lam=0.7) == []


def test_tokens_are_one_per_cjk_character_and_a_quarter_per_other_rounded_up() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1  # rounded up: a budget must not under-count
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("退款") == 2
    assert estimate_tokens("退款 refund") == 2 + math.ceil(7 / 4)
    assert estimate_tokens("x" * 300) == 75


def test_the_budget_keeps_rank_order_and_skips_what_does_not_fit() -> None:
    items = ["a" * 40, "b" * 400, "c" * 40, "d" * 40]  # 10, 100, 10, 10 tokens
    assert fit_budget(items, estimate_tokens, 25) == ["a" * 40, "c" * 40]
    assert fit_budget(items, estimate_tokens, 1000) == items
    assert fit_budget(items, estimate_tokens, 5) == []


def test_mmr_compares_relevance_on_the_same_scale_as_similarity() -> None:
    """Fused scores are tiny (1/61 ...): unnormalised, any overlap would outweigh relevance."""
    items = [
        ("a", 0.020, {"refund", "days", "seven"}),
        ("b", 0.0199, {"refund", "days", "card", "bank", "fee", "cash", "visa", "euro", "late"}),
        ("c", 0.001, {"weather"}),
    ]
    # b overlaps a by 2/10; normalised: 0.7 * 0.995 - 0.3 * 0.2 = 0.64 beats c's 0.035
    assert mmr(items, k=2, lam=0.7) == ["a", "b"]
