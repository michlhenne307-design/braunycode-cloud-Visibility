#!/usr/bin/env python3
"""Erzeugt die PWA-Icons ohne externe Abhaengigkeiten.

    python3 scripts/make_icons.py

Schreibt nach app/static/icons/. Reine Standardbibliothek, damit auf dem
Server kein Pillow noetig ist.
"""

import math
import os
import struct
import zlib

BG = (0x1C, 0x1F, 0x24)      # theme-color, wie im Replit-Entwurf
ACCENT = (0x3B, 0x82, 0xF6)  # primary
FG = (0xE6, 0xED, 0xF3)      # foreground
SS = 3                       # Supersampling gegen Treppenkanten

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "static", "icons")


def dist_to_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def inside_rounded_rect(px, py, x0, y0, x1, y1, radius):
    cx = min(max(px, x0 + radius), x1 - radius)
    cy = min(max(py, y0 + radius), y1 - radius)
    return math.hypot(px - cx, py - cy) <= radius


def blend(base, top, alpha):
    return tuple(round(b + (t - b) * alpha) for b, t in zip(base, top))


def render(size, maskable):
    """Zeichnet '>_' auf abgerundeten Grund. maskable = randlos mit Safe-Zone."""
    big = size * SS
    unit = big / 512.0
    corner = 0 if maskable else 114 * unit          # iOS-aehnlicher Radius
    inset = 96 * unit if maskable else 0            # Safe-Zone fuer Maskierung

    stroke = 34 * unit
    chevron_x0 = 150 * unit + inset * 0.55
    chevron_x1 = 258 * unit + inset * 0.2
    chevron_top = 170 * unit + inset * 0.55
    chevron_mid = 262 * unit
    chevron_bot = 354 * unit - inset * 0.55
    bar_x0 = 286 * unit
    bar_x1 = 372 * unit - inset * 0.4
    bar_y0 = 330 * unit - inset * 0.4
    bar_y1 = bar_y0 + stroke * 0.82

    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            r_acc = g_acc = b_acc = 0
            for sy in range(SS):
                py = y * SS + sy + 0.5
                for sx in range(SS):
                    px = x * SS + sx + 0.5

                    if maskable:
                        pixel = BG
                    elif inside_rounded_rect(px, py, 0, 0, big, big, corner):
                        pixel = BG
                    else:
                        pixel = None

                    if pixel is not None:
                        # Akzent-Verlauf diagonal ueber den Grund
                        ramp = min(1.0, max(0.0, (px + py) / (2 * big)))
                        pixel = blend(pixel, ACCENT, 0.16 + 0.20 * ramp)

                        d = min(
                            dist_to_segment(px, py, chevron_x0, chevron_top,
                                            chevron_x1, chevron_mid),
                            dist_to_segment(px, py, chevron_x1, chevron_mid,
                                            chevron_x0, chevron_bot),
                        )
                        if d <= stroke / 2:
                            pixel = FG
                        elif inside_rounded_rect(px, py, bar_x0, bar_y0, bar_x1, bar_y1,
                                                 (bar_y1 - bar_y0) / 2):
                            pixel = ACCENT if maskable else FG

                    if pixel is None:
                        pixel = (0, 0, 0)
                    r_acc += pixel[0]
                    g_acc += pixel[1]
                    b_acc += pixel[2]

            n = SS * SS
            row += bytes((r_acc // n, g_acc // n, b_acc // n))
        rows.append(bytes(row))
    return rows


def write_png(path, rows, size):
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(png)
    return len(png)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    targets = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
        ("apple-touch-icon.png", 180, False),
    ]
    for name, size, maskable in targets:
        path = os.path.abspath(os.path.join(OUT_DIR, name))
        written = write_png(path, render(size, maskable), size)
        print(f"{name:26} {size}x{size}  {written} bytes")


if __name__ == "__main__":
    main()
