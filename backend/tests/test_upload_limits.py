"""
tests/test_upload_limits.py
---------------------------
Tests for the capped upload reader (F15). Unit-tests read_upload_capped with a
minimal UploadFile-like stub — no auth/DB/endpoint needed. The endpoint wiring
(file-count cap + per-file cap) is exercised end-to-end by the course-upload
tests added in PR-16.
"""

import io

import pytest
from fastapi import HTTPException

from courses.router import read_upload_capped


class _FakeUpload:
    """Minimal UploadFile stand-in exposing the async read(size) interface."""

    def __init__(self, data: bytes, filename: str = "f.pdf") -> None:
        self._buf = io.BytesIO(data)
        self.filename = filename

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


async def test_reads_small_file_fully():
    data = b"hello world" * 10
    out = await read_upload_capped(_FakeUpload(data), max_bytes=10_000)
    assert out == data


async def test_reads_file_at_exact_cap():
    data = b"x" * 2048
    out = await read_upload_capped(_FakeUpload(data), max_bytes=2048)
    assert out == data  # equal to cap is allowed


async def test_rejects_file_over_cap():
    data = b"x" * (3 * 1024 * 1024)  # 3 MB, spans multiple 1 MB chunks
    with pytest.raises(HTTPException) as exc:
        await read_upload_capped(_FakeUpload(data, "big.pdf"), max_bytes=1024 * 1024)
    assert exc.value.status_code == 413
    assert "big.pdf" in exc.value.detail


async def test_empty_file_returns_empty_bytes():
    out = await read_upload_capped(_FakeUpload(b""), max_bytes=1000)
    assert out == b""
