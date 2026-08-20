"""Extract per-page placed-image identity + rendered visibility from a PDF via PyMuPDF.

Prints one JSON line to stdout:
{
  "openable": true,
  "page_count": N,
  "images": [
    {"page": 1, "bbox": [x0, y0, x1, y1], "width": W, "height": H, "xref": X,
     "digest": "<hex md5 of decoded pixels>", "thumb": [256 floats in 0..1],
     "render_nonwhite_ratio": 0.42}
  ]
}

Identity uses two content-level signals, never geometry as the deciding factor:
- ``digest``: PyMuPDF's decoded-pixel MD5 (stable only when pixels are byte-identical).
- ``thumb``: a 16x16 absolute grayscale thumbnail (area-average resample, values 0..1,
  NOT centered) so pure-white vs pure-color vs line-art remain distinguishable and small
  re-encode/color-space shifts keep near-identical images close.
Actual visibility is measured per bbox: the page region under the placed rectangle is
rendered and its non-white pixel ratio recorded (pure-white / near-white / transparent
=> 0.0). Grayscale, line-art, and white-background figures are fine because this uses a
white/non-white threshold, never a "must have color" rule.
"""

import argparse
import json
import sys

import fitz

THUMB = 16
NONWHITE_THRESHOLD = 250  # grayscale value below this counts as non-white


def _grayscale_thumb(pix: fitz.Pixmap) -> list[float]:
    """Return a THUMB x THUMB absolute grayscale thumbnail as floats in [0, 1]."""
    if pix.n - pix.alpha > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    width, height, n = pix.width, pix.height, pix.n
    samples = pix.samples
    stride = n
    gray = [0.0] * (width * height)
    if n >= 3:
        for i in range(width * height):
            off = i * stride
            gray[i] = (0.299 * samples[off] + 0.587 * samples[off + 1] + 0.114 * samples[off + 2]) / 255.0
    else:
        for i in range(width * height):
            gray[i] = samples[i * stride] / 255.0

    thumb = [0.0] * (THUMB * THUMB)
    for ty in range(THUMB):
        y0 = ty * height // THUMB
        y1 = (ty + 1) * height // THUMB
        for tx in range(THUMB):
            x0 = tx * width // THUMB
            x1 = (tx + 1) * width // THUMB
            total = 0.0
            count = 0
            for y in range(y0, y1):
                base = y * width
                for x in range(x0, x1):
                    total += gray[base + x]
                    count += 1
            thumb[ty * THUMB + tx] = total / count if count else 0.0
    return thumb


def _render_nonwhite_ratio(page: fitz.Page, bbox) -> float:
    """Render the placed-image bbox clip and return the non-white pixel ratio."""
    clip = fitz.Rect(*bbox)
    if clip.is_empty or clip.width <= 0 or clip.height <= 0:
        return 0.0
    try:
        pix = page.get_pixmap(clip=clip, colorspace=fitz.csGRAY)
    except Exception:
        return 0.0
    samples = pix.samples
    total = pix.width * pix.height
    if total <= 0:
        return 0.0
    nonwhite = 0
    for value in samples:
        if value < NONWHITE_THRESHOLD:
            nonwhite += 1
    return nonwhite / total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        doc = fitz.open(args.input)
    except Exception as exc:  # noqa: BLE001 - report any open failure as integrity error
        print(
            json.dumps(
                {
                    "openable": False,
                    "error_code": "DOCUMENT_INTEGRITY_ERROR",
                    "page_count": 0,
                    "images": [],
                    "detail": str(exc),
                }
            )
        )
        return 0
    images = []
    for page_index, page in enumerate(doc):
        for info in page.get_image_info(hashes=True, xrefs=True):
            bbox = info.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            digest = info.get("digest")
            if isinstance(digest, bytes):
                digest = digest.hex()
            elif not isinstance(digest, str):
                digest = ""
            xref = info.get("xref")
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            thumb: list[float] = []
            try:
                pix = fitz.Pixmap(doc, xref)
                thumb = _grayscale_thumb(pix)
                pix = None
            except Exception:
                thumb = []
            images.append(
                {
                    "page": page_index + 1,
                    "bbox": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
                    "width": width,
                    "height": height,
                    "xref": int(xref or 0),
                    "digest": digest if isinstance(digest, str) else "",
                    "thumb": thumb,
                    "render_nonwhite_ratio": round(_render_nonwhite_ratio(page, bbox), 6),
                }
            )
    print(json.dumps({"openable": True, "page_count": len(doc), "images": images}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
