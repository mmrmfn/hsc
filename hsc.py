#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Jay
#
# HSC is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. It is distributed WITHOUT ANY WARRANTY; see the LICENSE file.
"""
HSC 1.0 - Huffman file compressor.

Usage:
    python3 hsc.py c input.txt                # -> input.txt.hsc
    python3 hsc.py d input.txt.hsc            # -> input.txt
    python3 hsc.py c input.txt out.hsc        # explicit output name
    python3 hsc.py d out.hsc restored.txt
    python3 hsc.py --version

.hsc file format (v1.0):
    [3 bytes]  magic "HSC"
    [1 byte ]  format version (0x10 = 1.0)
    [4 bytes]  original length (big-endian)
    [256 x 4]  byte frequency table (so the decoder can rebuild the tree)
    [rest]     packed bitstream
"""
import heapq
import os
import struct
import sys
from collections import Counter

VERSION = "1.0"
MAGIC = b"HSC"
FORMAT_VERSION = 0x10          # high nibble = major, low nibble = minor -> 1.0
EXT = ".hsc"
HEADER_SIZE = 3 + 1 + 4 + 256 * 4


# ---------- building the tree ----------

def build_tree(freqs):
    """Merge the two rarest nodes repeatedly until one tree remains.
    A leaf is an int (the byte value). An internal node is a (left, right) tuple."""
    # Sorting matters: encoder and decoder MUST build the identical tree,
    # so tie-breaking can't depend on dict insertion order.
    # Number symbols only AFTER dropping zero counts: the encoder sees only the
    # bytes that occur, the decoder sees all 256, and both must agree.
    used = sorted((sym, f) for sym, f in freqs.items() if f)
    heap = [(f, i, sym) for i, (sym, f) in enumerate(used)]
    heapq.heapify(heap)
    if len(heap) == 1:                       # edge case: only one distinct byte
        sym = heap[0][2]
        return (sym, sym)
    i = len(heap)
    while len(heap) > 1:
        f1, _, a = heapq.heappop(heap)
        f2, _, b = heapq.heappop(heap)
        heapq.heappush(heap, (f1 + f2, i, (a, b)))
        i += 1
    return heap[0][2]


def build_codes(node, prefix="", table=None):
    """Walk the tree: going left adds '0', going right adds '1'."""
    if table is None:
        table = {}
    if isinstance(node, int):
        table[node] = prefix or "0"
    else:
        build_codes(node[0], prefix + "0", table)
        build_codes(node[1], prefix + "1", table)
    return table


# ---------- compress / decompress ----------

def compress(data: bytes) -> bytes:
    freqs = Counter(data)
    header = MAGIC + bytes([FORMAT_VERSION]) + struct.pack(">I", len(data))
    header += b"".join(struct.pack(">I", freqs.get(b, 0)) for b in range(256))
    if not data:
        return header

    codes = build_codes(build_tree(freqs))
    bits = "".join(codes[b] for b in data)
    bits += "0" * (-len(bits) % 8)           # pad to a whole byte
    body = int(bits, 2).to_bytes(len(bits) // 8, "big")
    return header + body


def decompress(blob: bytes) -> bytes:
    if len(blob) < HEADER_SIZE or blob[:3] != MAGIC:
        raise ValueError("not an .hsc file (bad magic bytes)")
    ver = blob[3]
    if ver >> 4 != FORMAT_VERSION >> 4:
        raise ValueError(f"unsupported .hsc version {ver >> 4}.{ver & 0xF}")

    (n,) = struct.unpack(">I", blob[4:8])
    freqs = {b: struct.unpack(">I", blob[8 + 4*b: 12 + 4*b])[0] for b in range(256)}
    if n == 0:
        return b""

    root = build_tree(freqs)                 # same freqs -> same tree
    out = bytearray()
    node = root
    for byte in blob[HEADER_SIZE:]:
        for k in range(7, -1, -1):
            node = node[(byte >> k) & 1]
            if isinstance(node, int):
                out.append(node)
                if len(out) == n:
                    return bytes(out)
                node = root
    return bytes(out)


# ---------- command line ----------

def default_output(mode, src):
    if mode == "c":
        return src + EXT
    if src.endswith(EXT):
        return src[: -len(EXT)]
    return src + ".out"


def main(argv):
    if argv and argv[0] in ("-v", "--version"):
        print(f"HSC {VERSION}")
        return 0
    if len(argv) not in (2, 3) or argv[0] not in ("c", "d"):
        print(__doc__.strip())
        return 1

    mode, src = argv[0], argv[1]
    dst = argv[2] if len(argv) == 3 else default_output(mode, src)
    if os.path.abspath(src) == os.path.abspath(dst):
        print("error: input and output are the same file", file=sys.stderr)
        return 1

    with open(src, "rb") as f:
        raw = f.read()
    try:
        result = compress(raw) if mode == "c" else decompress(raw)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with open(dst, "wb") as f:
        f.write(result)

    msg = f"HSC {VERSION}: {src} -> {dst}  ({len(raw):,} -> {len(result):,} bytes"
    if mode == "c" and raw:
        msg += f", {len(result) / len(raw) * 100:.1f}% of original"
    print(msg + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
