from __future__ import annotations

import logging

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


def test_reversed(versions: Versions) -> None:
    # Act
    output = list(reversed(versions))

    # Assert
    assert output[0].name == "3.9"
    assert output[-1].name == "3.14"


def test_from_json() -> None:
    # Arrange
    json_data = {
        "3.14": {
            "branch": "main",
            "pep": 745,
            "status": "feature",
            "first_release": "2025-10-01",
            "end_of_life": "2030-10",
            "release_manager": "Hugo van Kemenade",
        },
        "3.13": {
            "branch": "3.13",
            "pep": 719,
            "status": "bugfix",
            "first_release": "2024-10-07",
            "end_of_life": "2029-10",
            "release_manager": "Thomas Wouters",
        },
    }

    # Act
    versions = list(Versions.from_json(json_data))

    # Assert
    assert versions == [
        Version(name="3.13", status="stable", branch_or_tag=""),
        Version(name="3.14", status="in development", branch_or_tag=""),
    ]


def test_from_json_warning(caplog) -> None:
    # Arrange
    json_data = {
        "2.8": {"branch": "2.8", "pep": 404, "status": "ex-release"},
        "3.16": {
            "branch": "",
            "pep": 826,
            "status": "",
            "first_release": "2027-10-06",
            "end_of_life": "2032-10",
            "release_manager": "Savannah Ostrowski",
        },
    }

    # Act
    with caplog.at_level(logging.WARNING):
        versions = list(Versions.from_json(json_data))

    # Assert: both should be skipped
    assert versions == []
    assert "Saw invalid version status 'ex-release'" in caplog.text
    assert "Saw invalid version status ''" in caplog.text


def test_current_stable(versions) -> None:
    # Act
    current_stable = versions.current_stable

    # Assert
    assert current_stable.name == "3.13"
    assert current_stable.status == "stable"


def test_current_dev(versions) -> None:
    # Act
    current_dev = versions.current_dev

    # Assert
    assert current_dev.name == "3.14"
    assert current_dev.status == "in development"


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
