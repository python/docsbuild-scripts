import pytest

from build_docs import format_seconds


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0.4, "0s"),
        (0.5, "0s"),
        (0.6, "1s"),
        (1.5, "2s"),
        (30, "30s"),
        (60, "1m 0s"),
        (185, "3m 5s"),
        (454, "7m 34s"),
        (7456, "2h 4m 16s"),
        (30.1, "30s"),
        (60.2, "1m 0s"),
        (185.3, "3m 5s"),
        (454.4, "7m 34s"),
        (7456.5, "2h 4m 16s"),
    ],
)
def test_format_seconds(seconds: float, expected: str) -> None:
    assert format_seconds(seconds) == expected
