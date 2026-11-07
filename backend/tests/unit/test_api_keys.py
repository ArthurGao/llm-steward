import pytest

from llm_steward.services.namespace_service import parse_key


@pytest.mark.parametrize(
    ("token", "parsed"),
    [
        ("stw_ab12cd34_s3cr3t", ("ab12cd34", "s3cr3t")),
        ("stw_ab12cd34_sec_ret-with_underscores", ("ab12cd34", "sec_ret-with_underscores")),
        ("stw_ab12cd34_", None),
        ("stw__secret", None),
        ("sk_ab12cd34_secret", None),
        ("stw_ab12cd34", None),
        ("", None),
    ],
)
def test_parse_key(token: str, parsed: tuple[str, str] | None) -> None:
    assert parse_key(token) == parsed
