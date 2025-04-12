from build_docs import Version, Versions


def test_filter_default() -> None:
    # Arrange
    versions = Versions([
        Version(name="3.14", status="in development", branch_or_tag=""),
        Version(name="3.13", status="stable", branch_or_tag=""),
        Version(name="3.12", status="stable", branch_or_tag=""),
        Version(name="3.11", status="security-fixes", branch_or_tag=""),
        Version(name="3.10", status="security-fixes", branch_or_tag=""),
        Version(name="3.9", status="security-fixes", branch_or_tag=""),
    ])

    # Act
    filtered = versions.filter()

    # Assert
    assert filtered == [
        Version(name="3.14", status="in development", branch_or_tag=""),
        Version(name="3.13", status="stable", branch_or_tag=""),
        Version(name="3.12", status="stable", branch_or_tag=""),
    ]


def test_filter_one() -> None:
    # Arrange
    versions = Versions([
        Version(name="3.14", status="in development", branch_or_tag=""),
        Version(name="3.13", status="stable", branch_or_tag=""),
        Version(name="3.12", status="stable", branch_or_tag=""),
        Version(name="3.11", status="security-fixes", branch_or_tag=""),
        Version(name="3.10", status="security-fixes", branch_or_tag=""),
        Version(name="3.9", status="security-fixes", branch_or_tag=""),
    ])

    # Act
    filtered = versions.filter(["3.13"])

    # Assert
    assert filtered == [Version(name="3.13", status="security-fixes", branch_or_tag="")]


def test_filter_multiple() -> None:
    # Arrange
    versions = Versions([
        Version(name="3.14", status="in development", branch_or_tag=""),
        Version(name="3.13", status="stable", branch_or_tag=""),
        Version(name="3.12", status="stable", branch_or_tag=""),
        Version(name="3.11", status="security-fixes", branch_or_tag=""),
        Version(name="3.10", status="security-fixes", branch_or_tag=""),
        Version(name="3.9", status="security-fixes", branch_or_tag=""),
    ])

    # Act
    filtered = versions.filter(["3.13", "3.14"])

    # Assert
    assert filtered == [
        Version(name="3.14", status="in development", branch_or_tag=""),
        Version(name="3.13", status="security-fixes", branch_or_tag=""),
    ]
