"""Local deterministic institutional portraits; never calls an AI service."""
import io

from PIL import Image, ImageEnhance, ImageOps, ImageStat, ImageFilter

SIZE = 512  # Full-size profile; avatar_sprite owns every small portrait.


def oriented(raw, rotation=0):
    if isinstance(rotation, bool) or rotation not in (0, 90, 180, 270):
        raise ValueError('invalid rotation')
    from app.core.photo_guard import decode
    image = decode(raw, max_bytes=20 * 1024 * 1024, max_pixels=50_000_000)
    return image.rotate(-rotation, expand=True)


def portrait(raw, rotation=0, enhance=True):
    image = oriented(raw, rotation)
    if enhance:
        image = ImageOps.autocontrast(image, cutoff=0.5)
        mean = sum(ImageStat.Stat(image).mean) / 3
        if mean < 110:
            image = ImageEnhance.Brightness(image).enhance(min(1.7, 110 / max(mean, 1)))
        image = image.filter(ImageFilter.UnsharpMask(radius=1, percent=65, threshold=3))
    image = ImageOps.fit(image, (SIZE, SIZE), method=Image.Resampling.LANCZOS, centering=(0.5, 0.3))
    output = io.BytesIO()
    image.save(output, format='WEBP', quality=90, method=4)
    return output.getvalue()
