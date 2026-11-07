"""UUIDv7 (RFC 9562): time-ordered ids, so primary keys sort by creation and work as cursors."""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """48-bit Unix milliseconds, version 7, 12 + 62 random bits, RFC 4122 variant."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = rand >> 68  # 12 bits
    rand_b = rand & ((1 << 62) - 1)  # 62 bits
    value = (ms & ((1 << 48) - 1)) << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b
    return uuid.UUID(int=value)
