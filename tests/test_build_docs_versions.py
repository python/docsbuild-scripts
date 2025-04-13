from __future__ import annotations

import pytest

from build_docs import Version, Versions


@pytest.fixture
def versions() -> Versions:
    return Versions([
        Version(name="3.14", status="in development", branch_or_tag=""),
        Version(name="3.13", status="stable", branch_or_tag=""),
        Version(name="3.12", status="stable", branch_or_tag=""),
        Version(name="3.11", status="security-fixes", branch_or_tag=""),
        Version(name="3.10", status="security-fixes", branch_or_tag=""),
        Version(name="3.9", status="security-fixes", branch_or_tag=""),
    ])


def test_filter_default(versions) -> None:
    # Act
    filtered = versions.filter()

    # Assert
    assert filtered == [
        Version(name="3.14", status="in development", branch_or_tag=""),
        Version(name="3.13", status="stable", branch_or_tag=""),
        Version(name="3.12", status="stable", branch_or_tag=""),
    ]


def test_filter_one(versions) -> None:
    # Act
    filtered = versions.filter(["3.13"])

    # Assert
    assert filtered == [Version(name="3.13", status="security-fixes", branch_or_tag="")]


def test_filter_multiple(versions) -> None:
    # Act
    filtered = versions.filter(["3.13", "3.14"])

    # Assert
    assert filtered == [
        Version(name="3.14", status="in development", branch_or_tag=""),
        Version(name="3.13", status="security-fixes", branch_or_tag=""),
    ]
