# -*- coding: utf-8 -*-
"""生成程序图标：只用标准库（zlib 手写 PNG，再包成 ICO）。

输出：
  app/static/favicon.ico   浏览器标签页图标（窗口/任务栏也用它）
  app/static/icon.png      256x256 PNG，快捷方式与文档用

图形：圆角方块 + 向下的白色箭头 + 底部托盘横线。
"""
from __future__ import annotations

import os
import struct
import zlib

BG_TOP = (66, 148, 255)
BG_BOTTOM = (26, 90, 208)
SS = 4  # 每个像素 4x4 采样做抗锯齿


def _in_round_rect(x: float, y: float, r: float) -> bool:
    """(0,1) 坐标下的圆角正方形内部判定。"""
    cx, cy = 0.5, 0.5
    hw = hh = 0.5
    dx = abs(x - cx) - (hw - r)
    dy = abs(y - cy) - (hh - r)
    if dx <= 0 and dy <= 0:
        return True
    dx = max(dx, 0.0)
    dy = max(dy, 0.0)
    return dx * dx + dy * dy <= r * r


def _in_tri(x: float, y: float, a, b, c) -> bool:
    def s(p, q, r):
        return (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])
    d1, d2, d3 = s((x, y), a, b), s((x, y), b, c), s((x, y), c, a)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def _in_arrow(x: float, y: float) -> bool:
    # 竖杆
    if 0.437 <= x <= 0.563 and 0.205 <= y <= 0.560:
        return True
    # 箭头
    if _in_tri(x, y, (0.297, 0.548), (0.703, 0.548), (0.500, 0.782)):
        return True
    # 托盘横线
    if 0.300 <= x <= 0.700 and 0.842 <= y <= 0.884:
        return True
    if 0.300 <= x <= 0.380 and 0.820 <= y <= 0.906:
        return True
    if 0.620 <= x <= 0.700 and 0.820 <= y <= 0.906:
        return True
    return False


def render(size: int) -> bytes:
    """返回 RGBA 原始像素（行优先）。"""
    rows = []
    step = 1.0 / (size * SS)
    for py in range(size):
        row = bytearray()
        for px in range(size):
            bg_hits = 0
            fg_hits = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = (px * SS + sx + 0.5) * step
                    y = (py * SS + sy + 0.5) * step
                    if not _in_round_rect(x, y, 0.235):
                        continue
                    bg_hits += 1
                    if _in_arrow(x, y):
                        fg_hits += 1
            total = SS * SS
            if not bg_hits:
                row += b"\x00\x00\x00\x00"
                continue
            t = (py + 0.5) / size
            br = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
            bg = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
            bb = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
            w = fg_hits / bg_hits
            r = int(br * (1 - w) + 255 * w)
            g = int(bg * (1 - w) + 255 * w)
            b = int(bb * (1 - w) + 255 * w)
            a = int(round(255 * bg_hits / total))
            row += bytes((r, g, b, a))
        rows.append(bytes(row))
    return b"".join(rows)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png(size: int) -> bytes:
    raw = render(size)
    stride = size * 4
    scanlines = b"".join(b"\x00" + raw[i * stride:(i + 1) * stride] for i in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(scanlines, 9))
            + _chunk(b"IEND", b""))


def ico(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    blobs = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, blob in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    return head + entries + b"".join(blobs)


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    static = os.path.join(os.path.dirname(here), "app", "static")
    os.makedirs(static, exist_ok=True)
    ico_path = os.path.join(static, "favicon.ico")
    png_path = os.path.join(static, "icon.png")
    with open(ico_path, "wb") as f:
        f.write(ico())
    with open(png_path, "wb") as f:
        f.write(png(256))
    print(f"[ok] {ico_path} ({os.path.getsize(ico_path)} B)")
    print(f"[ok] {png_path} ({os.path.getsize(png_path)} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
