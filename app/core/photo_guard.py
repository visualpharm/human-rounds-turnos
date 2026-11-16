"""Local decoding for professional portraits and admin-uploaded center photos.

Centers use normalize(): decode actual bytes, apply EXIF orientation, bound
pixels and dimensions, and re-encode as JPEG. Professional portraits share
decode() with their own byte/pixel limits, portrait rendering and private
original storage. Patient account photos have a separate upload pipeline.

Degradation contract: HEIC/HEIF decodes only where a real decoder is
installed (pillow-heif in requirements.txt). Without it a HEIC upload is
REFUSED with the concrete next step ("export it as JPG or PNG"), never a
500, never stored half-processed. No network, no AI: everything is local.

PhotoRejected carries the HTTP status and the user-facing error dict in
the exact shape each caller already returns — remedies, not raw errors.
"""

import io
import warnings

MAX_BYTES = 5 * 1024 * 1024  # 5 MB of upload payload (pre-decode)
MAX_PIXELS = 24_000_000     # center limit; professional callers supply their own cap
JPEG_QUALITY = 88

# HEIC container brands (ISO BMFF `ftyp`): Apple camera output and the
# generic HEIF containers a phone or messenger may hand over.
_HEIC_BRANDS = {
    b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs",
    b"heif", b"mif1", b"msf1",
}

_HEIF_OPENED = False


class PhotoRejected(Exception):
    """Upload refused. `.status` is the HTTP status, `.error` the user-facing
    dict (Spanish, remedy-first) each caller returns as its JSON body."""

    def __init__(self, status, error):
        super().__init__(str(error))
        self.status = status
        self.error = error


def heif_available():
    """True when a real HEIC decoder is importable. Patchable in tests."""
    try:
        import pillow_heif  # noqa: F401
    except Exception:  # noqa: BLE001 — optional dependency, degrade never crash
        return False
    return True


def _open_heif_support():
    """Register pillow-heif's opener once per process."""
    global _HEIF_OPENED
    if _HEIF_OPENED:
        return
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception as exc:  # optional native codec may import but fail to initialize
        raise _heic_remedy() from exc
    _HEIF_OPENED = True


def sniff(data):
    """Container family by magic bytes: 'png' | 'jpg' | 'webp' | 'heic' | None.
    Says what the file claims, never that it decodes — callers that only
    need the claim (Gravatar fetches, remote image preflight) keep using
    this; stored uploads must also pass decode() or normalize()."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    # ISO BMFF: size(4) + 'ftyp' + major brand(4)
    if data[4:8] == b"ftyp" and data[8:12].lower() in _HEIC_BRANDS:
        return "heic"
    return None


def _heic_remedy():
    return PhotoRejected(400, {"error": "La foto está en HEIC (formato de "
        "iPhone) y este servidor todavía no lo lee. Guardala como JPG o PNG y "
        "volvé a subirla."})


def _reject_invalid():
    return PhotoRejected(400, {"error": "La foto no se pudo leer (puede estar "
        "dañada). Probá con otra foto en JPG, PNG o WEBP."})


def _flatten(img):
    """JPEG has no alpha: composite any transparency over white so a
    transparent PNG/WebP never turns black on re-encode."""
    from PIL import Image
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        base = Image.new("RGBA", img.size, (255, 255, 255, 255))
        base.alpha_composite(img.convert("RGBA"))
        return base.convert("RGB")
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def decode(raw, max_bytes=MAX_BYTES, max_pixels=MAX_PIXELS):
    """Decode real bytes into an upright, detached RGB image within bounds."""
    from PIL import Image, UnidentifiedImageError

    if not raw:
        raise PhotoRejected(400, {"error": "Falta la foto"})
    if len(raw) > max_bytes:
        raise PhotoRejected(413, {"error": f"La foto supera los {max_bytes // (1024 * 1024)} MB. Elegí una más liviana."})

    family = sniff(raw)
    if family is None:
        raise PhotoRejected(400, {"error": "La foto tiene que ser JPG, PNG, WEBP o HEIC."})
    if family == "heic" and not heif_available():
        raise _heic_remedy()
    if family == "heic":
        _open_heif_support()

    try:
        with warnings.catch_warnings():
            # A declared-massive image is a bomb, not a photo: the warning
            # Pillow raises for oversized headers becomes our rejection.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as img:
                if img.width * img.height > max_pixels:
                    raise PhotoRejected(400, {"error": f"La foto supera los {max_pixels // 1_000_000} megapíxeles. Elegí una de menor resolución."})
                # load() forces the FULL decode now: a truncated body fails
                # here instead of in every browser that opens it later
                img.load()
                from PIL import ImageOps
                upright = ImageOps.exif_transpose(img)
    except PhotoRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        # Pillow's own >89 MP tripwire (we escalated its warning to an error)
        raise PhotoRejected(400, {"error": f"La foto supera los {max_pixels // 1_000_000} megapíxeles. Elegí una de menor resolución."})
    except (UnidentifiedImageError, OSError, ValueError, EOFError, SyntaxError):
        # magic bytes matched, the decode didn't: a lying or truncated file
        raise _reject_invalid()

    return _flatten(upright)


def normalize(raw, max_dim=1600, max_bytes=MAX_BYTES):
    """Decode, orient and resize a photo into a bounded JPEG."""
    work = decode(raw, max_bytes=max_bytes)
    if max(work.width, work.height) > max_dim:
        from PIL import Image
        work.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    work.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    data = out.getvalue()
    if len(data) > max_bytes:
        # bounded dimensions + q88 virtually never get here; one retry at a
        # lower quality, then the honest refusal
        out = io.BytesIO()
        work.save(out, format="JPEG", quality=70, optimize=True)
        data = out.getvalue()
        if len(data) > max_bytes:
            raise PhotoRejected(413, {"error": "La foto supera los 5 MB"})
    return data, "jpg", "image/jpeg"
