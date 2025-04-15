from __future__ import annotations

import pytest

from build_docs import Version


def test_equality() -> None:
    # Arrange
    version1 = Version(name="3.13", status="stable", branch_or_tag="3.13")
    version2 = Version(name="3.13", status="stable", branch_or_tag="3.13")

    # Act / Assert
    assert version1 == version2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("3.13", "-rrequirements.txt"),
        ("3.10", "standard-imghdr"),
        ("3.7", "sphinx==2.3.1"),
        ("3.5", "sphinx==1.8.4"),
    ],
)
def test_requirements(name: str, expected: str) -> None:
    # Arrange
    version = Version(name=name, status="stable", branch_or_tag="")

    # Act / Assert
    assert expected in version.requirements


def test_requirements_error() -> None:
    # Arrange
    version = Version(name="2.8", status="ex-release", branch_or_tag="")

    # Act / Assert
    with pytest.raises(ValueError, match="unreachable"):
        _ = version.requirements


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("EOL", "never"),
        ("security-fixes", "yearly"),
        ("stable", "daily"),
    ],
)
def test_changefreq(status: str, expected: str) -> None:
    # Arrange
    version = Version(name="3.13", status=status, branch_or_tag="")

    # Act / Assert
    assert version.changefreq == expected


def test_url() -> None:
    # Arrange
    version = Version(name="3.13", status="stable", branch_or_tag="")

    # Act / Assert
    assert version.url == "https://docs.python.org/3.13/"


def test_title() -> None:
    # Arrange
    version = Version(name="3.14", status="in development", branch_or_tag="")

    # Act / Assert
    assert version.title == "Python 3.14 (in development)"


@pytest.mark.parametrize(
    ("name", "status", "expected"),
    [
        ("3.15", "in development", "dev (3.15)"),
        ("3.14", "pre-release", "pre (3.14)"),
        ("3.13", "stable", "3.13"),
    ],
)
def test_picker_label(name: str, status: str, expected: str) -> None:
    # Arrange
    version = Version(name=name, status=status, branch_or_tag="")

    # Act / Assert
    assert version.picker_label == expected
