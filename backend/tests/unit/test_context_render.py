import uuid

from llm_steward.services.retrieval_service import RetrievedItem, _render


def _item(kind: str, n: int, content: str, title: str | None, verified: bool) -> RetrievedItem:
    return RetrievedItem(
        kind=kind,  # type: ignore[arg-type]
        id=uuid.UUID(int=n),
        content=content,
        title=title,
        status="verified" if verified else "active",
        verified=verified,
        score=0.1,
    )


MEM = _item("memory", 1, "Drinks black coffee", None, False)
KB = _item("entry", 2, "3-5 business days", "Refunds", True)
DRAFTY = _item("entry", 3, "Plus a fee abroad", "Cross-border", False)
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


def test_a_documents_further_passages_are_indented_under_its_cited_line() -> None:
    doc = RetrievedItem(
        kind="document",
        id=uuid.UUID(int=4),
        content="Refunds take 3-5 days\n…\nShipping is free",
        title="Policy",
        status="active",
        verified=False,
        score=0.1,
        passages=("Refunds take 3-5 days", "Shipping is free"),
    )
    assert _render([doc]) == (
        "## 知识库\n"
        f"- [kb:{uuid.UUID(int=4)}] Policy：Refunds take 3-5 days（未验证）\n"
        "  - Shipping is free"
    )
