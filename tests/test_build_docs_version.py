from build_docs import Version


def test_filter_default() -> None:
    # Arrange
    versions = [
        Version("3.14", status="feature"),
        Version("3.13", status="bugfix"),
        Version("3.12", status="bugfix"),
        Version("3.11", status="security"),
        Version("3.10", status="security"),
        Version("3.9", status="security"),
    ]

    # Act
    filtered = Version.filter(versions)

    # Assert
    assert filtered == [
        Version("3.14", status="feature"),
        Version("3.13", status="bugfix"),
        Version("3.12", status="bugfix"),
    ]


def test_filter_one() -> None:
    # Arrange
    versions = [
        Version("3.14", status="feature"),
        Version("3.13", status="bugfix"),
        Version("3.12", status="bugfix"),
        Version("3.11", status="security"),
        Version("3.10", status="security"),
        Version("3.9", status="security"),
    ]

    # Act
    filtered = Version.filter(versions, ["3.13"])

    # Assert
    assert filtered == [Version("3.13", status="security")]


def test_filter_multiple() -> None:
    # Arrange
    versions = [
        Version("3.14", status="feature"),
        Version("3.13", status="bugfix"),
        Version("3.12", status="bugfix"),
        Version("3.11", status="security"),
        Version("3.10", status="security"),
        Version("3.9", status="security"),
    ]

    # Act
    filtered = Version.filter(versions, ["3.13", "3.14"])

    # Assert
    assert filtered == [
        Version("3.14", status="feature"),
        Version("3.13", status="security"),
    ]