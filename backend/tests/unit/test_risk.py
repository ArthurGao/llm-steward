"""Rule-based risk (design §5): high-risk keywords and PII. Pure functions, no LLM."""

import pytest

from llm_steward.errors import Unprocessable
from llm_steward.models.enums import RiskLevel
from llm_steward.pipeline.risk import RiskPolicy, assess, find_pii

POLICY = RiskPolicy(high_risk_keywords=("退款政策", "price"), pii_action="redact")


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("call me on 13812345678 tomorrow", "phone"),
        ("call +86 138 0013 8000 tomorrow", "phone"),  # grouped, with the country code
        ("call 138-0013-8000", "phone"),
        ("call 8613800138000", "phone"),
        ("call +13812345678", "phone"),  # a bare '+' before the number (matched before 23797aa)
        ("ID 11010519491231002X please", "cn_id"),
        ("card 4111 1111 1111 1111", "bank_card"),
        ("card 5555-5555-5555-4444", "bank_card"),  # digits above 4: Luhn doubling
        ("password: 13812345678abc", "secret"),  # one value, reported once (not also a phone)
        ("mail alice@example.com", "email"),
        ("key sk-abcdefghijklmnopqrstuvwx", "secret"),
        ("AKIAABCDEFGHIJKLMNOP leaked", "secret"),
        ("api_key = 9f8e7d6c5b4a3210", "secret"),
    ],
)
def test_pii_is_found(text: str, kind: str) -> None:
    assert [k for k, _ in find_pii(text)] == [kind]


@pytest.mark.parametrize(
    "text",
    [
        "order 12345678901 shipped",  # 11 digits, not a mobile pattern (starts with 12)
        "order 123-4567-8901 shipped",  # grouped like a phone, but not a mobile prefix
        "ID 110105194912310021",  # bad checksum: not an ID number
        "card 4111 1111 1111 1112",  # fails Luhn
        "refunds take 3 to 5 business days",
        "version 2026.11.09",
    ],
)
def test_ordinary_text_is_not_pii(text: str) -> None:
    assert find_pii(text) == []


def test_plain_knowledge_is_low_risk() -> None:
    result = assess("Refund time", "Refunds take 3 to 5 business days", POLICY)
    assert (result.level, result.reasons, result.content) == (
        RiskLevel.LOW,
        [],
        "Refunds take 3 to 5 business days",
    )


def test_a_keyword_makes_it_high() -> None:
    result = assess("新退款政策", "七天无理由", POLICY)
    assert result.level == RiskLevel.HIGH and result.reasons == ["keyword: 退款政策"]
    assert assess("Price list", "x", POLICY).level == RiskLevel.HIGH  # case-insensitive


def test_pii_is_redacted_and_makes_it_high() -> None:
    result = assess("Contact", "Reach Alice at alice@example.com or 13812345678", POLICY)
    assert result.level == RiskLevel.HIGH
    assert result.content == "Reach Alice at [REDACTED:email] or [REDACTED:phone]"
    assert result.reasons == ["pii: email", "pii: phone"]
    assert "alice@example.com" not in " ".join(result.reasons)  # reasons never repeat the value


def test_pii_can_be_blocked_or_allowed() -> None:
    with pytest.raises(Unprocessable, match="email"):
        assess("t", "alice@example.com", RiskPolicy(pii_action="block"))
    kept = assess("t", "alice@example.com", RiskPolicy(pii_action="allow"))
    assert (kept.content, kept.level) == ("alice@example.com", RiskLevel.HIGH)


def test_pii_in_the_title_is_redacted_too() -> None:
    result = assess("Call 13812345678", "body", POLICY)
    assert result.title == "Call [REDACTED:phone]"


def test_the_policy_comes_from_the_namespace_config() -> None:
    config = {"kb": {"high_risk_keywords": ["法律"]}, "memory": {"pii": {"action": "block"}}}
    assert RiskPolicy.from_config(config) == RiskPolicy(("法律",), "block")
    assert RiskPolicy.from_config({}) == RiskPolicy((), "redact")


def test_detection_stays_linear_on_hostile_input() -> None:
    import time

    start = time.perf_counter()
    for text in ("a" * 50_000, "a." * 25_000, "1 " * 25_000):
        assert find_pii(text) == []
    assert time.perf_counter() - start < 0.5  # was quadratic: a long local part with no '@'


def test_tags_are_screened_like_the_text() -> None:
    result = assess("t", "c", POLICY, tags=("billing", "alice@example.com"))
    assert result.tags == ("billing", "[REDACTED:email]")
    assert (result.level, result.reasons) == (RiskLevel.HIGH, ["pii: email"])
