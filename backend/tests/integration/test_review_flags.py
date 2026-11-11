"""Feedback-driven review (design §5 maintenance, P7d): knowledge that answers often and is often
rated unhelpful is flagged for review (`review_due_at`, by llm-steward itself, logged); a
reviewer's new version or verification clears it; the Admin lists what is due and what is worth
verifying. Both dialects."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select, update

from llm_steward.models import ChangeLog, KbEntry
from llm_steward.models.enums import ActorType, KbStatus, ObjectType
from llm_steward.services.governance_service import (
    CreateCollection,
    ReviseKbEntry,
    TransitionKbEntry,
)
from tests.conftest import AppClient, kb_entry_op, running_app

TOKEN = "admin-token-for-tests-0123456789abcdef"
ADMIN = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def c(migrated_url: str) -> Iterator[AppClient]:
    with running_app(migrated_url, settings={"admin_token": TOKEN}) as client:
        yield client


def _entry(c: AppClient, ns: uuid.UUID, collection: uuid.UUID, title: str, **counts: int) -> Any:
    entry = c.apply(kb_entry_op(ns, collection, KbStatus.ACTIVE, title=title))

    async def rated() -> None:
        async with c.sessions() as s, s.begin():
            await s.execute(update(KbEntry).where(KbEntry.id == entry.id).values(**counts))

    c.call(rated)
    return entry


def _flags(c: AppClient) -> list[ChangeLog]:
    async def read() -> list[ChangeLog]:
        async with c.sessions() as s:
            rows = await s.scalars(
                select(ChangeLog)
                .where(ChangeLog.object_type == ObjectType.KB_ENTRY)
                .order_by(ChangeLog.id)
            )
            return [  # the change that set it
                r
                for r in rows
                if r.before is not None
                and r.before.get("review_due_at") is None
                and (r.after or {}).get("review_due_at") is not None
            ]

    return c.call(read)


def test_often_unhelpful_knowledge_is_flagged_for_review_once(c: AppClient) -> None:
    _, ns = c.new_tenant("acme")
    col = c.apply(CreateCollection(ns, "faq")).id
    noisy = _entry(c, ns, col, "Noisy", hit_count=6, helpful_count=1, unhelpful_count=3)
    liked = _entry(c, ns, col, "Liked", hit_count=6, helpful_count=4, unhelpful_count=1)
    rare = _entry(c, ns, col, "Rare", hit_count=3, helpful_count=0, unhelpful_count=2)
    retired = _entry(c, ns, col, "Old", hit_count=9, helpful_count=0, unhelpful_count=5)
    c.apply(TransitionKbEntry(ns, retired.id, KbStatus.DEPRECATED))

    assert c.call(c.services.kb.flag_for_review) == 1
    assert c.get(KbEntry, noisy.id).review_due_at is not None
    for quiet in (liked, rare, retired):
        assert c.get(KbEntry, quiet.id).review_due_at is None
    [flag] = _flags(c)
    assert (flag.object_id, flag.actor_type) == (noisy.id, ActorType.SYSTEM)
    assert "3 of 4" in (flag.reason or "")  # why, in the history
    assert c.get(KbEntry, noisy.id).version == noisy.version  # a flag is not a new version

    assert c.call(c.services.kb.flag_for_review) == 0  # already flagged: not again
    assert len(_flags(c)) == 1


def test_a_reviewers_new_version_or_verification_clears_the_flag(c: AppClient) -> None:
    _, ns = c.new_tenant("acme")
    col = c.apply(CreateCollection(ns, "faq")).id
    revised = _entry(c, ns, col, "A", hit_count=6, helpful_count=0, unhelpful_count=4)
    verified = _entry(c, ns, col, "B", hit_count=6, helpful_count=0, unhelpful_count=4)
    assert c.call(c.services.kb.flag_for_review) == 2

    c.apply(ReviseKbEntry(ns, revised.id, content="Refunds take 7 business days"))
    c.apply(TransitionKbEntry(ns, verified.id, KbStatus.VERIFIED))
    assert c.get(KbEntry, revised.id).review_due_at is None
    assert c.get(KbEntry, verified.id).review_due_at is None


def test_the_admin_lists_what_is_due_and_what_is_worth_verifying(c: AppClient) -> None:
    _, ns = c.new_tenant("acme")
    col = c.apply(CreateCollection(ns, "faq")).id
    due = _entry(c, ns, col, "Due", hit_count=6, helpful_count=0, unhelpful_count=4)
    good = _entry(c, ns, col, "Good", hit_count=30, helpful_count=9, unhelpful_count=1)
    _entry(c, ns, col, "Few votes", hit_count=30, helpful_count=2, unhelpful_count=0)
    _entry(c, ns, col, "Plain", hit_count=1)
    c.call(c.services.kb.flag_for_review)

    def listed(review: str) -> list[str]:
        r = c.http.get(f"/admin/v1/ns/acme/kb/entries?review={review}", headers=ADMIN)
        assert r.status_code == 200, r.text
        return [e["id"] for e in r.json()["items"]]

    assert listed("due") == [str(due.id)]
    assert listed("verify") == [str(good.id)]  # active, well rated, with enough votes
    shown = c.http.get(f"/admin/v1/ns/acme/kb/entries/{due.id}", headers=ADMIN).json()
    assert shown["review_due_at"] is not None
    c.apply(TransitionKbEntry(ns, due.id, KbStatus.DEPRECATED))
    assert listed("due") == []  # taken out: nothing left to review


def test_after_a_review_only_newer_feedback_counts(c: AppClient) -> None:
    """Verified (or revised) after a flag, an entry is not flagged again for the feedback the
    reviewer already looked at; enough unhelpful feedback after the review flags it again."""
    _, ns = c.new_tenant("acme")
    col = c.apply(CreateCollection(ns, "faq")).id
    entry = _entry(c, ns, col, "A", hit_count=6, helpful_count=1, unhelpful_count=3)
    assert c.call(c.services.kb.flag_for_review) == 1
    c.apply(TransitionKbEntry(ns, entry.id, KbStatus.VERIFIED))  # reviewed
    assert c.call(c.services.kb.flag_for_review) == 0  # the same feedback: not again

    async def more_feedback(**counts: int) -> None:
        async with c.sessions() as s, s.begin():
            await s.execute(update(KbEntry).where(KbEntry.id == entry.id).values(**counts))

    c.call(more_feedback, hit_count=12, helpful_count=4)  # 6 hits since, all rated helpful
    assert c.call(c.services.kb.flag_for_review) == 0  # the old unhelpful ones do not count
    c.call(more_feedback, hit_count=9, helpful_count=1, unhelpful_count=4)  # 3 hits since: few
    assert c.call(c.services.kb.flag_for_review) == 0
    c.call(more_feedback, hit_count=12, unhelpful_count=6)  # 6 hits, 3 of 3 unhelpful since
    assert c.call(c.services.kb.flag_for_review) == 1
