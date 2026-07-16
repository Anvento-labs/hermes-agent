"""CRWD image metadata extract tools (camera receipts + screenshots).

Two LLM-callable tools that download an image, read EXIF / container metadata
with Pillow (+ pillow-heif for HEIC/HEIF), and return field **values** so the
model can judge authenticity. Neither tool computes a confidence score.

  - ``crwd_verify_camera_receipt`` — camera-relevant fields
  - ``crwd_verify_screenshot`` — screenshot-relevant fields

Privacy: GPS and MakerNote are exposed only as booleans (``has_gps``,
``has_makernote``) — never raw coordinates or MakerNote bytes.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from PIL import Image
from PIL.ExifTags import Base as ExifBase
from PIL.ExifTags import IFD

from tools.registry import registry

logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024

# EXIF tag numbers not always exposed as enum members on older Pillow builds.
_TAG_MAKERNOTE = 37500  # 0x927C
_TAG_GPS_IFD = 34853  # 0x8825
_TAG_ISO = 34855  # ISOSpeedRatings
_TAG_FOCAL_LENGTH = 37386
_TAG_EXPOSURE_TIME = 33434
_TAG_FNUMBER = 33437
_TAG_FLASH = 37385
_TAG_SENSING_METHOD = 37399
_TAG_SUBSEC_TIME_ORIGINAL = 37521
_TAG_OFFSET_TIME_ORIGINAL = 36881
_TAG_DATETIME_ORIGINAL = 36867

_HEIF_REGISTERED = False


def _register_heif_opener() -> None:
    """Register pillow-heif so Pillow can open HEIC/HEIF (idempotent)."""
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return
    try:
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
        _HEIF_REGISTERED = True
    except Exception as exc:  # pragma: no cover - env without wheel
        logger.warning("pillow-heif unavailable; HEIC open may fail: %s", exc)


def _image_url_shape_ok(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    if not url.startswith(("http://", "https://")):
        return False
    parsed = urlparse(url)
    return bool(parsed.netloc)


def _validate_image_url(url: str) -> bool:
    if not _image_url_shape_ok(url):
        return False
    from tools.url_safety import is_safe_url

    return is_safe_url(url)


def _download_image_sync(image_url: str, destination: Path) -> Path:
    """SSRF-safe sync download (redirect targets re-validated)."""
    from tools.url_safety import is_safe_url, redirect_target_from_response
    from tools.website_policy import check_website_access

    destination.parent.mkdir(parents=True, exist_ok=True)

    blocked = check_website_access(image_url)
    if blocked:
        raise PermissionError(blocked["message"])

    def _ssrf_redirect_guard(response: httpx.Response) -> None:
        redirect_url = redirect_target_from_response(response)
        if redirect_url and not is_safe_url(redirect_url):
            raise ValueError(
                f"Blocked redirect to private/internal address: {redirect_url}"
            )

    with httpx.Client(
        timeout=_TIMEOUT_S,
        follow_redirects=True,
        event_hooks={"response": [_ssrf_redirect_guard]},
    ) as client:
        response = client.get(
            image_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "image/*,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        cl = response.headers.get("content-length")
        if cl and int(cl) > _MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"Image too large ({int(cl)} bytes, max {_MAX_DOWNLOAD_BYTES})"
            )
        final_url = str(response.url)
        blocked = check_website_access(final_url)
        if blocked:
            raise PermissionError(blocked["message"])
        body = response.content
        if len(body) > _MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"Image too large ({len(body)} bytes, max {_MAX_DOWNLOAD_BYTES})"
            )
        destination.write_bytes(body)
    return destination


def _scalarize(value: Any) -> Any:
    """Convert EXIF values to JSON-friendly scalars (no bytes dumps)."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return None  # never emit raw binary (MakerNote etc.)
    if isinstance(value, (int, float, bool, str)):
        return value
    # IFDRational / fractions
    try:
        from fractions import Fraction

        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            num = int(value.numerator)
            den = int(value.denominator)
            if den == 0:
                return None
            if den == 1:
                return num
            return f"{num}/{den}"
        if isinstance(value, Fraction):
            return str(value)
    except Exception:
        pass
    if isinstance(value, (list, tuple)):
        # Avoid dumping GPS coordinate tuples — caller uses has_gps only.
        return None
    try:
        return str(value)
    except Exception:
        return None


def _exif_get(exif: Image.Exif, tag: int) -> Any:
    try:
        return exif.get(tag)
    except Exception:
        return None


def _exif_ifd_get(exif: Image.Exif, tag: int) -> Any:
    try:
        exif_ifd = exif.get_ifd(IFD.Exif)
    except Exception:
        return None
    if not exif_ifd:
        return None
    return exif_ifd.get(tag)


def _has_gps(exif: Image.Exif) -> bool:
    try:
        if _TAG_GPS_IFD in exif and exif[_TAG_GPS_IFD]:
            return True
    except Exception:
        pass
    try:
        gps = exif.get_ifd(IFD.GPSInfo)
        return bool(gps)
    except Exception:
        return False


def _has_makernote(exif: Image.Exif) -> bool:
    raw = _exif_ifd_get(exif, _TAG_MAKERNOTE)
    if raw is None:
        raw = _exif_get(exif, _TAG_MAKERNOTE)
    if raw is None:
        return False
    if isinstance(raw, (bytes, bytearray)):
        return len(raw) > 0
    return True


def _png_text(img: Image.Image) -> Optional[Dict[str, str]]:
    if (img.format or "").upper() != "PNG":
        return None
    text = getattr(img, "text", None) or {}
    if not text:
        return None
    out: Dict[str, str] = {}
    for key, val in text.items():
        if not isinstance(key, str):
            continue
        if isinstance(val, bytes):
            try:
                val = val.decode("utf-8", errors="replace")
            except Exception:
                continue
        if isinstance(val, str) and len(val) <= 512:
            out[key] = val
    return out or None


def extract_meta(path: Path) -> Dict[str, Any]:
    """Open image at ``path`` and return a flat metadata dict (shared extract)."""
    _register_heif_opener()
    with Image.open(path) as img:
        fmt = (img.format or "").upper() or None
        width, height = img.size
        png_text = _png_text(img)
        try:
            exif = img.getexif()
        except Exception:
            exif = None

    meta: Dict[str, Any] = {
        "format": fmt,
        "width": width,
        "height": height,
        "Make": None,
        "Model": None,
        "Software": None,
        "DateTimeOriginal": None,
        "Orientation": None,
        "ISO": None,
        "FocalLength": None,
        "ExposureTime": None,
        "FNumber": None,
        "Flash": None,
        "SensingMethod": None,
        "SubSecTimeOriginal": None,
        "OffsetTimeOriginal": None,
        "has_gps": False,
        "has_makernote": False,
        "png_text": png_text,
    }
    if not exif:
        return meta

    meta["Make"] = _scalarize(_exif_get(exif, ExifBase.Make) or _exif_get(exif, 271))
    meta["Model"] = _scalarize(_exif_get(exif, ExifBase.Model) or _exif_get(exif, 272))
    meta["Software"] = _scalarize(
        _exif_get(exif, ExifBase.Software) or _exif_get(exif, 305)
    )
    meta["Orientation"] = _scalarize(
        _exif_get(exif, ExifBase.Orientation) or _exif_get(exif, 274)
    )

    meta["DateTimeOriginal"] = _scalarize(
        _exif_ifd_get(exif, _TAG_DATETIME_ORIGINAL)
        or _exif_ifd_get(exif, ExifBase.DateTimeOriginal)
    )
    meta["ISO"] = _scalarize(
        _exif_ifd_get(exif, _TAG_ISO) or _exif_ifd_get(exif, ExifBase.ISOSpeedRatings)
    )
    meta["FocalLength"] = _scalarize(
        _exif_ifd_get(exif, _TAG_FOCAL_LENGTH)
        or _exif_ifd_get(exif, ExifBase.FocalLength)
    )
    meta["ExposureTime"] = _scalarize(
        _exif_ifd_get(exif, _TAG_EXPOSURE_TIME)
        or _exif_ifd_get(exif, ExifBase.ExposureTime)
    )
    meta["FNumber"] = _scalarize(
        _exif_ifd_get(exif, _TAG_FNUMBER) or _exif_ifd_get(exif, ExifBase.FNumber)
    )
    meta["Flash"] = _scalarize(
        _exif_ifd_get(exif, _TAG_FLASH) or _exif_ifd_get(exif, ExifBase.Flash)
    )
    meta["SensingMethod"] = _scalarize(
        _exif_ifd_get(exif, _TAG_SENSING_METHOD)
        or _exif_ifd_get(exif, getattr(ExifBase, "SensingMethod", _TAG_SENSING_METHOD))
    )
    meta["SubSecTimeOriginal"] = _scalarize(
        _exif_ifd_get(exif, _TAG_SUBSEC_TIME_ORIGINAL)
    )
    meta["OffsetTimeOriginal"] = _scalarize(
        _exif_ifd_get(exif, _TAG_OFFSET_TIME_ORIGINAL)
    )
    meta["has_gps"] = _has_gps(exif)
    meta["has_makernote"] = _has_makernote(exif)
    return meta


def _null_camera_fields() -> Dict[str, Any]:
    return {
        "format": None,
        "width": None,
        "height": None,
        "Make": None,
        "Model": None,
        "Software": None,
        "DateTimeOriginal": None,
        "Orientation": None,
        "ISO": None,
        "FocalLength": None,
        "ExposureTime": None,
        "FNumber": None,
        "Flash": None,
        "SensingMethod": None,
        "SubSecTimeOriginal": None,
        "OffsetTimeOriginal": None,
        "has_gps": False,
        "has_makernote": False,
        "error": None,
    }


def _null_screenshot_fields() -> Dict[str, Any]:
    return {
        "format": None,
        "width": None,
        "height": None,
        "Make": None,
        "Model": None,
        "Software": None,
        "DateTimeOriginal": None,
        "ISO": None,
        "FocalLength": None,
        "ExposureTime": None,
        "FNumber": None,
        "has_gps": False,
        "has_makernote": False,
        "png_text": None,
        "error": None,
    }


def camera_payload(meta: Dict[str, Any], error: Optional[str] = None) -> Dict[str, Any]:
    base = _null_camera_fields()
    if error:
        base["error"] = error
        return base
    for key in base:
        if key == "error":
            continue
        if key in meta:
            base[key] = meta[key]
    base["error"] = None
    return base


def screenshot_payload(meta: Dict[str, Any], error: Optional[str] = None) -> Dict[str, Any]:
    base = _null_screenshot_fields()
    if error:
        base["error"] = error
        return base
    for key in base:
        if key == "error":
            continue
        if key in meta:
            base[key] = meta[key]
    base["error"] = None
    return base


def _run_extract(image_url: str, kind: str) -> str:
    type_name = (
        "crwd_verify_camera_receipt"
        if kind == "camera"
        else "crwd_verify_screenshot"
    )
    empty = (
        camera_payload({}, error="missing image_url")
        if kind == "camera"
        else screenshot_payload({}, error="missing image_url")
    )
    empty["_type"] = type_name

    if not image_url or not isinstance(image_url, str) or not image_url.strip():
        return json.dumps(empty, ensure_ascii=False)

    image_url = image_url.strip()
    if not _validate_image_url(image_url):
        payload = (
            camera_payload({}, error="invalid or blocked image_url")
            if kind == "camera"
            else screenshot_payload({}, error="invalid or blocked image_url")
        )
        payload["_type"] = type_name
        return json.dumps(payload, ensure_ascii=False)

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        _download_image_sync(image_url, tmp_path)
        meta = extract_meta(tmp_path)
        payload = (
            camera_payload(meta)
            if kind == "camera"
            else screenshot_payload(meta)
        )
        payload["_type"] = type_name
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        logger.info("crwd image meta extract failed (%s): %s", kind, exc)
        payload = (
            camera_payload({}, error=str(exc)[:300])
            if kind == "camera"
            else screenshot_payload({}, error=str(exc)[:300])
        )
        payload["_type"] = type_name
        return json.dumps(payload, ensure_ascii=False)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def crwd_verify_camera_receipt_tool(args: Dict[str, Any], **_kw: Any) -> str:
    return _run_extract(args.get("image_url") or "", "camera")


def crwd_verify_screenshot_tool(args: Dict[str, Any], **_kw: Any) -> str:
    return _run_extract(args.get("image_url") or "", "screenshot")


CRWD_VERIFY_CAMERA_RECEIPT_SCHEMA = {
    "name": "crwd_verify_camera_receipt",
    "description": (
        "Extract EXIF/container metadata from a camera photo of a physical "
        "receipt (JPEG/HEIC/etc.). Returns field values only (Make, Model, "
        "DateTimeOriginal, capture settings, has_gps, has_makernote, …) — does "
        "NOT compute a confidence score; judge authenticity from the values. "
        "Never returns GPS coordinates or MakerNote bytes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "HTTP(S) URL of the receipt photo attachment.",
            },
        },
        "required": ["image_url"],
    },
}

CRWD_VERIFY_SCREENSHOT_SCHEMA = {
    "name": "crwd_verify_screenshot",
    "description": (
        "Extract EXIF/container metadata from an order/review/app screenshot. "
        "Returns field values only (format, Make/Model if any, Software, "
        "has_gps, has_makernote, png_text, …) — does NOT compute a confidence "
        "score; judge authenticity from the values. Never returns GPS "
        "coordinates or MakerNote bytes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "HTTP(S) URL of the screenshot attachment.",
            },
        },
        "required": ["image_url"],
    },
}


registry.register(
    name="crwd_verify_camera_receipt",
    toolset="crwd",
    schema=CRWD_VERIFY_CAMERA_RECEIPT_SCHEMA,
    handler=crwd_verify_camera_receipt_tool,
    emoji="📷",
)

registry.register(
    name="crwd_verify_screenshot",
    toolset="crwd",
    schema=CRWD_VERIFY_SCREENSHOT_SCHEMA,
    handler=crwd_verify_screenshot_tool,
    emoji="📱",
)
