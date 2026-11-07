import time
import uuid

from llm_steward.ids import uuid7


def test_version_variant_and_embedded_time() -> None:
    before = time.time_ns() // 1_000_000
    value = uuid7()
    after = time.time_ns() // 1_000_000
    assert value.version == 7
    assert value.variant == uuid.RFC_4122
    assert before <= value.int >> 80 <= after


def test_ids_from_later_milliseconds_sort_later() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first < second


def test_unique() -> None:
    assert len({uuid7() for _ in range(10_000)}) == 10_000
