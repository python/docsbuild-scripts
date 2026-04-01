from pathlib import Path
from unittest.mock import patch

import pytest

from build_docs import Version, Versions, build_robots_txt, format_seconds


@pytest.mark.parametrize(
    ("seconds", "expected"),
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


@patch("build_docs.chgrp")
def test_build_robots_txt(mock_chgrp, tmp_path) -> None:
    versions = Versions([
        Version(name="3.14", status="EOL", branch_or_tag="3.14"),
        Version(name="3.15", status="EOL", branch_or_tag="3.15"),
        Version(name="3.16", status="security-fixes", branch_or_tag="3.16"),
        Version(name="3.17", status="stable", branch_or_tag="2.17"),
    ])

    build_robots_txt(versions, tmp_path, group="", skip_cache_invalidation=True, http=None)

    result = (tmp_path / "robots.txt").read_text()
    assert "Disallow: /3.14/" in result
    assert "Disallow: /3.15/" in result
    assert "/3.16/" not in result
    assert "/3.17/" not in result
