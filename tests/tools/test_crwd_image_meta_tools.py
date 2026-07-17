"""Tests for CRWD image metadata extract tools (no network)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tools import crwd_image_meta_tools as t

# Synthetic JPEG with Make/Model/DateTimeOriginal/ISO/capture tags + GPS + MakerNote.
# Generated offline with piexif; coordinates/MakerNote bytes must not appear in tool JSON.
_CAMERA_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/4QFfRXhpZgAATU0AKgAAAAgABgEPAAIAAAAGAAAAVgEQAAIAAAAK"
    "AAAAXAESAAMAAAABAAEAAAExAAIAAAAFAAAAZodpAAQAAAABAAAAa4glAAQAAAABAAAA9QAAAABBcHBs"
    "ZQBpUGhvbmUgMTQAMTYuMAAABoKaAAUAAAABAAAAtYKdAAUAAAABAAAAvYgnAAMAAAABAEAAAJADAAIA"
    "AAAUAAAAxZIKAAUAAAABAAAA2ZJ8AAcAAAAUAAAA4QAAAAEAAAB4AAAAEgAAAAoyMDI2OjA3OjEwIDE0"
    "OjIyOjAxAAAAADkAAAAKZmFrZS1tYWtlcm5vdGUtYnl0ZXMABAABAAIAAAACTgAAAAACAAUAAAADAAAB"
    "JwADAAIAAAACVwAAAAAEAAUAAAADAAABPwAAACUAAAABAAAALgAAAAEAAAAAAAAAAQAAAHoAAAABAAAA"
    "GQAAAAEAAAAAAAAAAf/bAEMACAYGBwYFCAcHBwkJCAoMFA0MCwsMGRITDxQdGh8eHRocHCAkLicgIiwj"
    "HBwoNyksMDE0NDQfJzk9ODI8LjM0Mv/bAEMBCQkJDAsMGA0NGDIhHCEyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMv/AABEIAFAAQAMBIgACEQEDEQH/xAAfAAABBQEB"
    "AQEBAQAAAAAAAAAAAQIDBAUGBwgJCgv/xAC1EAACAQMDAgQDBQUEBAAAAX0BAgMABBEFEiExQQYTUWEH"
    "InEUMoGRoQgjQrHBFVLR8CQzYnKCCQoWFxgZGiUmJygpKjQ1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2Rl"
    "ZmdoaWpzdHV2d3h5eoOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU"
    "1dbX2Nna4eLj5OXm5+jp6vHy8/T19vf4+fr/xAAfAQADAQEBAQEBAQEBAAAAAAAAAQIDBAUGBwgJCgv/"
    "xAC1EQACAQIEBAMEBwUEBAABAncAAQIDEQQFITEGEkFRB2FxEyIygQgUQpGhscEJIzNS8BVictEKFiQ0"
    "4SXxFxgZGiYnKCkqNTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqCg4SFhoeIiYqS"
    "k5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2dri4+Tl5ufo6ery8/T19vf4"
    "+fr/2gAMAwEAAhEDEQA/APf6KKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiii"
    "gAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigD//2Q=="
)


@pytest.fixture
def camera_jpeg(tmp_path: Path) -> Path:
    path = tmp_path / "camera.jpg"
    path.write_bytes(base64.b64decode(_CAMERA_JPEG_B64))
    return path


@pytest.fixture
def empty_jpeg(tmp_path: Path) -> Path:
    path = tmp_path / "empty.jpg"
    Image.new("RGB", (12, 12), "red").save(path, format="JPEG")
    return path


@pytest.fixture
def screenshot_png(tmp_path: Path) -> Path:
    path = tmp_path / "shot.png"
    info = PngInfo()
    info.add_text("Software", "Screenshot")
    Image.new("RGB", (117, 253), "black").save(path, format="PNG", pnginfo=info)
    return path


class TestExtractMeta:
    def test_camera_jpeg_fields(self, camera_jpeg: Path):
        meta = t.extract_meta(camera_jpeg)
        payload = t.camera_payload(meta)
        assert payload["format"] == "JPEG"
        assert payload["Make"] == "Apple"
        assert payload["Model"] == "iPhone 14"
        assert payload["DateTimeOriginal"] == "2026:07:10 14:22:01"
        assert payload["ISO"] == 64
        assert payload["has_gps"] is True
        assert payload["has_makernote"] is True
        assert "confidence" not in payload
        raw = json.dumps(payload)
        assert "fake-makernote" not in raw
        assert "122" not in raw  # longitude must not leak

    def test_empty_jpeg_nulls(self, empty_jpeg: Path):
        payload = t.camera_payload(t.extract_meta(empty_jpeg))
        assert payload["format"] == "JPEG"
        assert payload["Make"] is None
        assert payload["has_gps"] is False
        assert payload["has_makernote"] is False

    def test_screenshot_png_text(self, screenshot_png: Path):
        payload = t.screenshot_payload(t.extract_meta(screenshot_png))
        assert payload["format"] == "PNG"
        assert payload["Make"] is None
        assert payload["has_gps"] is False
        assert payload["has_makernote"] is False
        assert payload["png_text"] == {"Software": "Screenshot"}
        assert "confidence" not in payload

    def test_heic_opens(self, tmp_path: Path):
        pytest.importorskip("pillow_heif")
        path = tmp_path / "tiny.heic"
        Image.new("RGB", (24, 24), "blue").save(path, format="HEIF")
        payload = t.camera_payload(t.extract_meta(path))
        assert payload["format"] in {"HEIF", "HEIC"}
        assert payload["width"] == 24
        assert payload["height"] == 24


class TestToolHandlers:
    def test_camera_tool_uses_download(self, camera_jpeg: Path):
        def _fake_dl(url: str, dest: Path) -> Path:
            dest.write_bytes(camera_jpeg.read_bytes())
            return dest

        with patch.object(t, "_validate_image_url", return_value=True), patch.object(
            t, "_download_image_sync", side_effect=_fake_dl
        ):
            out = json.loads(
                t.crwd_verify_camera_receipt_tool(
                    {"image_url": "https://cdn.example.com/r.jpg"}
                )
            )
        assert out["_type"] == "crwd_verify_camera_receipt"
        assert out["Make"] == "Apple"
        assert out["has_gps"] is True
        assert out["error"] is None
        assert "confidence" not in out

    def test_screenshot_tool_uses_download(self, screenshot_png: Path):
        def _fake_dl(url: str, dest: Path) -> Path:
            dest.write_bytes(screenshot_png.read_bytes())
            return dest

        with patch.object(t, "_validate_image_url", return_value=True), patch.object(
            t, "_download_image_sync", side_effect=_fake_dl
        ):
            out = json.loads(
                t.crwd_verify_screenshot_tool(
                    {"image_url": "https://cdn.example.com/s.png"}
                )
            )
        assert out["_type"] == "crwd_verify_screenshot"
        assert out["png_text"] == {"Software": "Screenshot"}
        assert out["error"] is None
        assert "confidence" not in out

    def test_bad_url(self):
        out = json.loads(
            t.crwd_verify_camera_receipt_tool({"image_url": "not-a-url"})
        )
        assert out["_type"] == "crwd_verify_camera_receipt"
        assert out["error"]
        assert out["Make"] is None

    def test_missing_url(self):
        out = json.loads(t.crwd_verify_screenshot_tool({}))
        assert out["error"]
        assert out["_type"] == "crwd_verify_screenshot"

    def test_download_failure(self):
        with patch.object(t, "_validate_image_url", return_value=True), patch.object(
            t, "_download_image_sync", side_effect=RuntimeError("boom")
        ):
            out = json.loads(
                t.crwd_verify_camera_receipt_tool(
                    {"image_url": "https://cdn.example.com/x.jpg"}
                )
            )
        assert out["error"]
        assert "boom" in out["error"]
        assert out["has_gps"] is False
