import io
import json
import zipfile
from unittest import mock

import pytest
import urllib3

from build_docs import BuildMetadata, DocBuilder, Language, Version


@pytest.fixture
def mock_http():
    return mock.Mock(spec=urllib3.PoolManager)


@pytest.fixture
def doc_builder(tmp_path):
    version = Version(name="3.13", status="stable", branch_or_tag="3.13")
    language = Language(
        iso639_tag="fr",
        name="French",
        translated_name="français",
        in_prod=True,
        html_only=False,
        sphinxopts=[],
    )
    build_meta = BuildMetadata(_version=version, _language=language)
    cpython_repo = mock.Mock()

    return DocBuilder(
        build_meta=build_meta,
        cpython_repo=cpython_repo,
        docs_by_version_content=b"",
        switchers_content=b"",
        build_root=tmp_path,
        www_root=tmp_path / "www",
        select_output="no-html",
        quick=False,
        group="docs",
        log_directory=tmp_path / "logs",
        skip_cache_invalidation=True,
        theme="python-docs-theme",
        fetch_artifacts=True,
        github_token="test-token",
    )


def create_mock_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("python-3.13-fr-docs.epub", b"epub content")
        zf.writestr("python-3.13-fr-docs-text.zip", b"text content")
    return buffer.getvalue()


def test_download_non_html_artifacts_success(doc_builder, mock_http):
    # Setup mock API responses
    list_response_data = {
        "artifacts": [
            # HTML artifact (should be skipped)
            {
                "id": 111,
                "name": "python-3.13-fr-docs-html.zip",
                "archive_download_url": "https://api.github.com/download/111",
                "created_at": "2026-07-09T12:00:00Z",
            },
            # Logs artifact (should be skipped)
            {
                "id": 222,
                "name": "python-3.13-fr-pdf-logs.zip",
                "archive_download_url": "https://api.github.com/download/222",
                "created_at": "2026-07-09T12:00:00Z",
            },
            # Matching older artifact (should be skipped in favor of 444)
            {
                "id": 333,
                "name": "python-3.13-fr-docs.epub",
                "archive_download_url": "https://api.github.com/download/333",
                "created_at": "2026-07-09T11:00:00Z",
            },
            # Matching newer artifact (should be downloaded)
            {
                "id": 444,
                "name": "python-3.13-fr-docs.epub",
                "archive_download_url": "https://api.github.com/download/444",
                "created_at": "2026-07-09T13:00:00Z",
            },
            # Other version (should be skipped)
            {
                "id": 555,
                "name": "python-3.12-fr-docs.epub",
                "archive_download_url": "https://api.github.com/download/555",
                "created_at": "2026-07-09T12:00:00Z",
            },
        ]
    }

    mock_zip_content = create_mock_zip()

    def mock_request(method, url, headers=None, **kwargs):
        if "artifacts" in url:
            return urllib3.HTTPResponse(
                body=json.dumps(list_response_data).encode("utf-8"),
                status=200,
            )
        elif "download/444" in url:
            return urllib3.HTTPResponse(
                body=mock_zip_content,
                status=200,
            )
        return urllib3.HTTPResponse(status=404)

    mock_http.request.side_effect = mock_request

    # Run execution
    doc_builder.download_non_html_artifacts(mock_http)

    # Assertions
    dist_dir = doc_builder.checkout / "Doc" / "dist"
    assert dist_dir.exists()
    assert (dist_dir / "python-3.13-fr-docs.epub").read_bytes() == b"epub content"
    assert (dist_dir / "python-3.13-fr-docs-text.zip").read_bytes() == b"text content"

    # Verify requests were made
    assert mock_http.request.call_count >= 2


def test_download_non_html_artifacts_no_token(doc_builder, mock_http, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    doc_builder.github_token = None

    with pytest.raises(ValueError, match="GitHub token is required"):
        doc_builder.download_non_html_artifacts(mock_http)


def test_download_non_html_artifacts_no_matching(doc_builder, mock_http):
    list_response_data = {"artifacts": []}
    mock_http.request.return_value = urllib3.HTTPResponse(
        body=json.dumps(list_response_data).encode("utf-8"),
        status=200,
    )

    with pytest.raises(RuntimeError, match="No matching non-HTML artifacts found"):
        doc_builder.download_non_html_artifacts(mock_http)


def test_download_non_html_artifacts_api_error(doc_builder, mock_http):
    mock_http.request.return_value = urllib3.HTTPResponse(
        body=b"Internal Server Error",
        status=500,
    )

    with pytest.raises(RuntimeError, match="Failed to fetch artifacts list"):
        doc_builder.download_non_html_artifacts(mock_http)
