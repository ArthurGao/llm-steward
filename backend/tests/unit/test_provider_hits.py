"""How a KB provider's chunk hits become one hit per external id (design §8 search contract)."""

from llm_steward.providers.base import ProviderHit, group_hits


def _hits(*rows: tuple[str, float]) -> list[ProviderHit]:
    return [ProviderHit(eid, score, f"{eid}@{score}") for eid, score in rows]


def test_one_hit_per_id_carrying_its_best_chunks_best_id_first() -> None:
    hits = _hits(("d1", 0.5), ("e1", 0.9), ("d1", 0.8), ("d1", 0.1), ("d1", 0.6), ("e2", 0.7))
    grouped = group_hits(hits, top_k=3)
    assert [(h.external_id, h.score, h.text, h.passages) for h in grouped] == [
        ("e1", 0.9, "e1@0.9", ("e1@0.9",)),
        ("d1", 0.8, "d1@0.8", ("d1@0.8", "d1@0.6", "d1@0.5")),  # its best three; 0.1 dropped
        ("e2", 0.7, "e2@0.7", ("e2@0.7",)),
    ]


def test_top_k_counts_ids_not_chunks() -> None:
    hits = _hits(("a", 0.9), ("a", 0.8), ("b", 0.7), ("c", 0.6))
    assert [h.external_id for h in group_hits(hits, top_k=2)] == ["a", "b"]
    assert group_hits(hits, top_k=2, per_id=1)[0].passages == ("a@0.9",)
