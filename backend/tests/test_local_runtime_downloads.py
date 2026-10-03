"""Reliability tests for resumable local-runtime and model downloads."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.local_runtime.downloads import (
    DownloadCancelled,
    _is_transient_download_error,
    download_with_fallback,
)


def _stream_response(*, status: int, length: int, chunks):
    response = MagicMock()
    response.status_code = status
    response.headers = {"content-length": str(length)}
    response.raise_for_status.return_value = None
    response.iter_bytes.return_value = iter(chunks)
    context = MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False
    return context


def test_transient_disconnect_is_retried_from_the_saved_byte_offset():
    def interrupted_chunks():
        yield b"abc"
        raise httpx.RemoteProtocolError("peer closed early")

    first = _stream_response(status=200, length=6, chunks=interrupted_chunks())
    second = _stream_response(status=206, length=3, chunks=[b"def"])

    with TemporaryDirectory() as temp_dir:
        destination = Path(temp_dir) / "runtime.zip"
        with patch(
            "app.services.local_runtime.downloads.httpx.stream",
            side_effect=[first, second],
        ) as stream, patch(
            "app.services.local_runtime.downloads._persist_progress",
        ) as persist, patch(
            "app.services.local_runtime.downloads.time.sleep",
        ):
            result = download_with_fallback("task-1", ["https://example.test/runtime.zip"], destination)

        assert result.read_bytes() == b"abcdef"
        assert stream.call_count == 2
        assert stream.call_args_list[1].kwargs["headers"] == {"Range": "bytes=3-"}
        assert any("自动续传" in str(call.kwargs.get("error_message")) for call in persist.call_args_list)


def test_completed_atomic_download_is_reused_without_another_network_request():
    with TemporaryDirectory() as temp_dir:
        destination = Path(temp_dir) / "runtime.zip"
        destination.write_bytes(b"already complete")

        with patch(
            "app.services.local_runtime.downloads.httpx.stream",
        ) as stream, patch(
            "app.services.local_runtime.downloads._persist_progress",
        ) as persist:
            result = download_with_fallback(
                "task-1",
                ["https://example.test/runtime.zip"],
                destination,
            )

        assert result == destination
        assert result.read_bytes() == b"already complete"
        stream.assert_not_called()
        persist.assert_called_once_with(
            "task-1",
            downloaded_bytes=len(b"already complete"),
            total_bytes=len(b"already complete"),
            error_message=None,
        )


def test_completed_download_with_bad_checksum_is_replaced():
    good_digest = "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721"
    response = _stream_response(status=200, length=6, chunks=[b"abcdef"])

    with TemporaryDirectory() as temp_dir:
        destination = Path(temp_dir) / "model.gguf"
        destination.write_bytes(b"corrupt")

        with patch(
            "app.services.local_runtime.downloads.httpx.stream",
            return_value=response,
        ) as stream, patch(
            "app.services.local_runtime.downloads._persist_progress",
        ):
            result = download_with_fallback(
                "task-1",
                ["https://example.test/model.gguf"],
                destination,
                expected_sha256=good_digest,
            )

        assert result.read_bytes() == b"abcdef"
        stream.assert_called_once()


def test_only_network_and_retryable_http_errors_are_automatically_retried():
    request = httpx.Request("GET", "https://example.test/file")
    retryable = httpx.HTTPStatusError(
        "server error",
        request=request,
        response=httpx.Response(503, request=request),
    )
    permanent = httpx.HTTPStatusError(
        "not found",
        request=request,
        response=httpx.Response(404, request=request),
    )

    assert _is_transient_download_error(httpx.RemoteProtocolError("closed"))
    assert _is_transient_download_error(httpx.ReadTimeout("slow"))
    assert _is_transient_download_error(retryable)
    assert not _is_transient_download_error(permanent)
    assert not _is_transient_download_error(OSError("磁盘空间不足"))


def test_cancelling_a_stream_preserves_partial_bytes_and_does_not_retry():
    response = _stream_response(status=200, length=6, chunks=[b"abc", b"def"])
    with TemporaryDirectory() as temp_dir:
        destination = Path(temp_dir) / "model.gguf"
        with patch("app.services.local_runtime.downloads.httpx.stream", return_value=response) as stream, patch(
            "app.services.local_runtime.downloads._persist_progress"
        ) as persist:
            with pytest.raises(DownloadCancelled):
                download_with_fallback(
                    "task-1", ["https://example.test/model.gguf"], destination,
                    should_cancel=lambda: persist.call_count >= 1,
                )
        assert stream.call_count == 1
        assert not destination.exists()
        assert destination.with_suffix(".gguf.part").read_bytes() == b"abc"
