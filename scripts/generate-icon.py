"""Generate Siming.ico from the SVG favicon for use with PyInstaller."""
import struct
import zlib
from pathlib import Path

# The icon is a 64x64 ink-brush "墨" motif on a warm paper background.
# We render it as a simple raster ICO with multiple sizes.

def _create_png(width: int, height: int) -> bytes:
    """Create a minimal PNG with the Siming icon rendered as raw pixel data."""
    # We'll draw a simplified version of the ink-brush icon
    # Background: #f8f5ef (warm paper), Ink: gradient from #2c2417 to #7c5e2a

    bg_r, bg_g, bg_b = 0xf8, 0xf5, 0xef
    ink_dark = (0x2c, 0x24, 0x17)
    ink_mid = (0x4a, 0x3c, 0x28)
    ink_light = (0x7c, 0x5e, 0x2a)
    accent = (0x7c, 0x5e, 0x2a)

    # Create RGBA pixel rows
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            # Normalize to 0-1 range
            nx = x / width
            ny = y / height

            r, g, b = bg_r, bg_g, bg_b
            a = 255

            # Vertical bar (the brush stroke) — x: 0.28-0.34, y: 0.22-0.78
            if 0.28 <= nx <= 0.34 and 0.22 <= ny <= 0.78:
                t = (nx - 0.28) / 0.06
                r = int(ink_dark[0] * (1 - t * 0.3) + ink_mid[0] * t * 0.3)
                g = int(ink_dark[1] * (1 - t * 0.3) + ink_mid[1] * t * 0.3)
                b = int(ink_dark[2] * (1 - t * 0.3) + ink_mid[2] * t * 0.3)

            # Horizontal stroke 1 (top) — x: 0.41-0.69, y: 0.31-0.38
            if 0.41 <= nx <= 0.69 and 0.31 <= ny <= 0.38:
                t = (nx - 0.41) / 0.28
                r = int(ink_dark[0] * (1 - t) + ink_mid[0] * t)
                g = int(ink_dark[1] * (1 - t) + ink_mid[1] * t)
                b = int(ink_dark[2] * (1 - t) + ink_mid[2] * t)

            # Horizontal stroke 2 (middle) — x: 0.41-0.63, y: 0.47-0.53
            if 0.41 <= nx <= 0.63 and 0.47 <= ny <= 0.53:
                t = (nx - 0.41) / 0.22
                r = int(ink_mid[0] * (1 - t * 0.5) + ink_light[0] * t * 0.5)
                g = int(ink_mid[1] * (1 - t * 0.5) + ink_light[1] * t * 0.5)
                b = int(ink_mid[2] * (1 - t * 0.5) + ink_light[2] * t * 0.5)

            # Horizontal stroke 3 (bottom) — x: 0.41-0.56, y: 0.63-0.69
            if 0.41 <= nx <= 0.56 and 0.63 <= ny <= 0.69:
                t = (nx - 0.41) / 0.15
                r = int(ink_light[0] * (1 - t * 0.3) + ink_light[0] * t * 0.3)
                g = int(ink_light[1] * (1 - t * 0.3) + ink_light[1] * t * 0.3)
                b = int(ink_light[2] * (1 - t * 0.3) + ink_light[2] * t * 0.3)

            # Accent dot — cx: 0.75, cy: 0.75, r: 0.06
            dx = nx - 0.75
            dy = ny - 0.75
            if (dx * dx + dy * dy) <= 0.06 * 0.06:
                r, g, b = accent

            row.extend([r, g, b, a])
        rows.append(bytes(row))

    # Build PNG
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter: none
        raw.extend(row)

    png = b'\x89PNG\r\n\x1a\n'
    png += _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
    png += _chunk(b'IDAT', zlib.compress(bytes(raw), 9))
    png += _chunk(b'IEND', b'')
    return png


def _create_ico(sizes: list[int], output_path: Path) -> None:
    """Create a multi-size .ico file."""
    png_data_list = []
    for size in sizes:
        png_data_list.append(_create_png(size, size))

    # ICO header
    header = struct.pack('<HHH', 0, 1, len(sizes))

    # Calculate offsets
    data_offset = 6 + 16 * len(sizes)  # header + directory entries
    entries = bytearray()
    data = bytearray()

    for i, (size, png) in enumerate(zip(sizes, png_data_list)):
        entry_size = 0 if size >= 256 else size  # 0 means 256
        entries.extend(struct.pack('<BBBBHHII',
            entry_size, entry_size, 0, 0, 1, 32, len(png), data_offset + len(data)))
        data.extend(png)

    output_path.write_bytes(header + bytes(entries) + bytes(data))


if __name__ == '__main__':
    out = Path(__file__).resolve().parent.parent / 'backend' / 'Siming.ico'
    _create_ico([16, 24, 32, 48, 64, 128, 256], out)
    print(f'Icon created: {out} ({out.stat().st_size} bytes)')
