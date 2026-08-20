"""Generate a small fixture PDF (optionally with one placed image) for visibility tests.

Runs inside the PDF runtime (PyMuPDF). Usage:
    pdf_make_fixture_pdf.py --output out.pdf [--rect x0 y0 x1 y1]
        [--color red|green|blue|white|black] [--grayscale]
        [--pattern solid|checker|lineart|gradient]

With no --rect the PDF is a single blank page, which models a figure that disappeared
during rendering. --color sets the base color for solid / line-art patterns. --pattern
selects the drawn content. A solid white image is the adversarial "blank replacement".
"""

import argparse
import sys

import fitz


def _solid(samples, color):
    for i in range(0, len(samples), 3):
        samples[i], samples[i + 1], samples[i + 2] = color


def _checker(samples, width, height):
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 3
            v = 255 if ((x // 8) + (y // 8)) % 2 == 0 else 0
            samples[i] = samples[i + 1] = samples[i + 2] = v


def _gradient(samples, width, height):
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 3
            samples[i] = x * 255 // max(width - 1, 1)
            samples[i + 1] = y * 255 // max(height - 1, 1)
            samples[i + 2] = (x + y) * 255 // max(width + height - 2, 1)


def _lineart(samples, width, height, color):
    # White background with black (or colored) horizontal lines every 10 rows.
    for i in range(0, len(samples), 3):
        samples[i] = samples[i + 1] = samples[i + 2] = 255
    for y in range(0, height, 10):
        for x in range(width):
            i = (y * width + x) * 3
            samples[i], samples[i + 1], samples[i + 2] = color


COLORS = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--rect", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"))
    parser.add_argument("--grayscale", action="store_true")
    parser.add_argument("--color", default="red", choices=sorted(COLORS))
    parser.add_argument("--pattern", default="solid", choices=["solid", "checker", "lineart", "gradient"])
    args = parser.parse_args()

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if args.rect:
        rect = fitz.Rect(*args.rect)
        width = int(rect.width)
        height = int(rect.height)
        color = COLORS[args.color]
        if args.grayscale:
            # Single-channel gray: solid 128, or line-art with dark lines on white.
            if args.pattern == "lineart":
                samples = bytearray([255]) * (width * height)
                for y in range(0, height, 10):
                    for x in range(width):
                        samples[y * width + x] = 0
            else:
                samples = bytearray([128]) * (width * height)
            pix = fitz.Pixmap(fitz.csGRAY, width, height, bytes(samples), False)
        else:
            samples = bytearray(width * height * 3)
            if args.pattern == "solid":
                _solid(samples, color)
            elif args.pattern == "checker":
                _checker(samples, width, height)
            elif args.pattern == "gradient":
                _gradient(samples, width, height)
            elif args.pattern == "lineart":
                _lineart(samples, width, height, color)
            pix = fitz.Pixmap(fitz.csRGB, width, height, bytes(samples), False)
        page.insert_image(rect, stream=pix.tobytes("png"))
    doc.save(args.output)
    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
