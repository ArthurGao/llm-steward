import uuid

from llm_steward.services.retrieval_service import RetrievedItem, _render

MEM = RetrievedItem(
    "memory", uuid.UUID(int=1), "Drinks black coffee", None, "active", False, 0.1, kind="memory"
)
KB = RetrievedItem(
    "kb", uuid.UUID(int=2), "3-5 business days", "Refunds", "verified", True, 0.1, kind="entry"
)
DRAFTY = RetrievedItem(
    "kb", uuid.UUID(int=3), "Plus a fee abroad", "Cross-border", "active", False, 0.1, kind="entry"
)
M1, K2, K3 = (str(uuid.UUID(int=i)) for i in (1, 2, 3))


def test_sections_citations_and_verification_marks() -> None:
    assert _render([MEM, KB, DRAFTY]) == (
        f"## 关于用户\n- [mem:{M1}] Drinks black coffee\n\n"
        f"## 知识库\n- [kb:{K2}] Refunds：3-5 business days（已验证）\n"
        f"- [kb:{K3}] Cross-border：Plus a fee abroad（未验证）"
    )


def test_only_present_sections_and_nothing_for_no_results() -> None:
    assert _render([MEM]) == f"## 关于用户\n- [mem:{M1}] Drinks black coffee"
    assert _render([]) == ""
