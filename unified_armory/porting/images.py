"""Pure-Python TGA reader and TIFF writer.

Editing Kit tool.exe exports bitmaps as TGA. The kits' `tool bitmaps` imports
TIFF (Halo CE only accepts TIFF), so textures are converted on the way across.
"""
from __future__ import annotations

import struct
from pathlib import Path


def read_tga(path: Path) -> tuple[int, int, bytes]:
    """Return (width, height, RGBA bytes top-to-bottom). Supports 24/32-bit and 8-bit gray,
    uncompressed or RLE."""
    data = Path(path).read_bytes()
    id_len, cmap_type, img_type = data[0], data[1], data[2]
    if cmap_type:
        raise ValueError("color-mapped TGA not supported")
    w, h, bpp, desc = struct.unpack_from("<HHBB", data, 12)
    if img_type not in (2, 3, 10, 11) or bpp not in (8, 24, 32):
        raise ValueError(f"unsupported TGA (type {img_type}, {bpp} bpp)")
    px = bpp // 8
    pos = 18 + id_len
    count = w * h
    raw = bytearray()
    if img_type in (2, 3):
        raw = data[pos:pos + count * px]
    else:
        while len(raw) < count * px:
            hdr = data[pos]; pos += 1
            n = (hdr & 0x7F) + 1
            if hdr & 0x80:
                raw += data[pos:pos + px] * n; pos += px
            else:
                raw += data[pos:pos + n * px]; pos += n * px
    out = bytearray(count * 4)
    for i in range(count):
        p = raw[i * px:(i + 1) * px]
        if px == 1:
            r = g = b = p[0]; a = 255
        else:
            b, g, r = p[0], p[1], p[2]
            a = p[3] if px == 4 else 255
        out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    if not desc & 0x20:  # bottom-up origin -> flip
        row = w * 4
        out = b"".join(out[y * row:(y + 1) * row] for y in range(h - 1, -1, -1))
    return w, h, bytes(out)


def write_tiff(path: Path, width: int, height: int, rgba: bytes) -> None:
    """Uncompressed 8-bit RGBA TIFF (little endian, single strip)."""
    if len(rgba) != width * height * 4:
        raise ValueError("pixel data does not match size")
    entries = [
        (256, 4, 1, width), (257, 4, 1, height), (258, 3, 4, None), (259, 3, 1, 1), (262, 3, 1, 2),
        (273, 4, 1, None), (277, 3, 1, 4), (278, 4, 1, height), (279, 4, 1, len(rgba)),
        (284, 3, 1, 1), (338, 3, 1, 2),
    ]
    ifd_offset = 8
    ifd_size = 2 + len(entries) * 12 + 4
    bits_offset = ifd_offset + ifd_size
    pixel_offset = bits_offset + 8
    out = bytearray(b"II*\x00" + struct.pack("<I", ifd_offset))
    out += struct.pack("<H", len(entries))
    for tag, typ, n, val in entries:
        if tag == 258:
            val = bits_offset
        elif tag == 273:
            val = pixel_offset
        if typ == 3 and n == 1:
            out += struct.pack("<HHIHH", tag, typ, n, val, 0)
        else:
            out += struct.pack("<HHII", tag, typ, n, val)
    out += struct.pack("<I", 0)
    out += struct.pack("<4H", 8, 8, 8, 8)
    out += rgba
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(out))


def convert_to_tiff(src: Path, dst: Path) -> None:
    src = Path(src)
    if src.suffix.lower() in (".tif", ".tiff"):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(src.read_bytes())
        return
    if src.suffix.lower() != ".tga":
        raise ValueError(f"cannot convert {src.suffix} textures; export them as TGA or TIFF")
    write_tiff(dst, *read_tga(src))
