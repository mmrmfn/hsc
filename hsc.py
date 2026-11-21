#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Jay
#
# HSC is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. It is distributed WITHOUT ANY WARRANTY; see the LICENSE file.
"""
HSC 2.0 - "everybody guesses, the winner gets written" compressor.

Run `hsc --help` for usage. (Without the installer: `python3 hsc.py --help`.)

How it works
------------
For every byte, seven little models each guess what the byte will be:

    0  RLE           same as the previous byte
    1  RECORD        same as this byte one record ago   (sensor records repeat)
    2  RECORD-TREND  continues the trend across records (delta)
    3  BYTE-TREND    continues the trend byte-to-byte
    4  MATCH         "this 4-byte pattern happened before; what came next then?"
    5  ORDER-2       what last followed these 2 bytes
    6  ORDER-1       what last followed this 1 byte

If any model is right, we write only WHICH model won (usually ~1-2 bits,
because the coder learns who tends to win in each spot). If nobody is right
we write ESCAPE plus the error versus the RECORD guess - for noisy sensor
data that error is small, so it's cheap too.

The decoder runs the exact same models, so "model 2 won" is enough for it to
rebuild the byte. Everything is coded with an adaptive binary range coder
(the same kind LZMA/7-Zip uses), so bits can cost a fraction of a bit.

Record size ("stride") is auto-detected, so lidar/IMU/CAN-style fixed-size
records get their own per-byte-position statistics.

Streaming: memory use is constant (~10 MB) no matter how big the file is.

.hsc v2.0 file format:
    [3 bytes] magic "HSC"
    [1 byte ] format version 0x20 (= 2.0)
    [2 bytes] record stride (big-endian); 0 = data stored raw (didn't compress)
    [8 bytes] original length (big-endian)
    [rest   ] range-coded stream
"""
import heapq
import os
import struct
import sys

VERSION = "2.0"
MAGIC = b"HSC"
EXT = ".hsc"
V1, V2 = 0x10, 0x20


# =====================================================================
#  HSC 1.0 decoder (Huffman) - kept so old .hsc files still open
# =====================================================================

def _v1_tree(freqs):
    heap = [(f, i, s) for i, (s, f) in enumerate(sorted(freqs.items())) if f]
    heapq.heapify(heap)
    if len(heap) == 1:
        return (heap[0][2], heap[0][2])
    i = len(heap)
    while len(heap) > 1:
        f1, _, a = heapq.heappop(heap)
        f2, _, b = heapq.heappop(heap)
        heapq.heappush(heap, (f1 + f2, i, (a, b)))
        i += 1
    return heap[0][2]


def decompress_v1(blob: bytes) -> bytes:
    hdr = 8 + 1024
    (n,) = struct.unpack(">I", blob[4:8])
    freqs = {b: struct.unpack(">I", blob[8 + 4*b: 12 + 4*b])[0] for b in range(256)}
    if n == 0:
        return b""
    root = node = _v1_tree(freqs)
    out = bytearray()
    for byte in blob[hdr:]:
        for k in range(7, -1, -1):
            node = node[(byte >> k) & 1]
            if isinstance(node, int):
                out.append(node)
                if len(out) == n:
                    return bytes(out)
                node = root
    return bytes(out)


# =====================================================================
#  Range coder: codes single bits using adaptive probabilities
# =====================================================================

PROB_BITS = 11
PROB_ONE = 1 << PROB_BITS
PROB_INIT = PROB_ONE // 2
ADAPT = 4                      # lower = learns faster, higher = steadier
TOP = 1 << 24
MASK32 = 0xFFFFFFFF


class Encoder:
    def __init__(self, f):
        self.f = f
        self.low = 0
        self.range = MASK32
        self.cache = 0
        self.cache_size = 1
        self.out = bytearray()

    def bit(self, probs, i, b):
        p = probs[i]
        bound = (self.range >> PROB_BITS) * p
        if b:
            self.low += bound
            self.range -= bound
            probs[i] = p - (p >> ADAPT)
        else:
            self.range = bound
            probs[i] = p + ((PROB_ONE - p) >> ADAPT)
        while self.range < TOP:
            self.range <<= 8
            self._shift_low()

    def _shift_low(self):
        low = self.low
        if low < 0xFF000000 or low > MASK32:      # top byte is settled
            carry = low >> 32
            temp = self.cache
            while True:
                self.out.append((temp + carry) & 0xFF)
                temp = 0xFF
                self.cache_size -= 1
                if not self.cache_size:
                    break
            self.cache = (low >> 24) & 0xFF
            if len(self.out) >= 1 << 16:
                self.f.write(self.out)
                self.out.clear()
        self.cache_size += 1
        self.low = (low & 0x00FFFFFF) << 8

    def finish(self):
        for _ in range(5):
            self._shift_low()
        self.f.write(self.out)
        self.out.clear()


class Decoder:
    def __init__(self, f):
        self.f = f
        self.buf = b""
        self.pos = 0
        self.range = MASK32
        self.code = 0
        for _ in range(5):
            self.code = ((self.code << 8) | self._byte()) & MASK32

    def _byte(self):
        if self.pos >= len(self.buf):
            self.buf = self.f.read(1 << 16)
            self.pos = 0
            if not self.buf:
                return 0
        b = self.buf[self.pos]
        self.pos += 1
        return b

    def bit(self, probs, i):
        p = probs[i]
        bound = (self.range >> PROB_BITS) * p
        if self.code < bound:
            self.range = bound
            probs[i] = p + ((PROB_ONE - p) >> ADAPT)
            b = 0
        else:
            self.code -= bound
            self.range -= bound
            probs[i] = p - (p >> ADAPT)
            b = 1
        while self.range < TOP:
            self.range <<= 8
            self.code = ((self.code << 8) | self._byte()) & MASK32
        return b


def put_tree(enc, probs, base, nbits, value):
    """Code an nbits number one bit at a time; each bit gets its own learned probability."""
    m = 1
    for k in range(nbits - 1, -1, -1):
        b = (value >> k) & 1
        enc.bit(probs, base + m, b)
        m = (m << 1) | b


def get_tree(dec, probs, base, nbits):
    m = 1
    for _ in range(nbits):
        m = (m << 1) | dec.bit(probs, base + m)
    return m - (1 << nbits)


# =====================================================================
#  The guessing models
# =====================================================================

NAMES = ["RLE", "RECORD", "RECORD-TREND", "BYTE-TREND", "MATCH", "ORDER-2", "ORDER-1"]
ESC = 7                        # 7 models + escape = 8 symbols = 3 bits max
WIN_BITS = 22                  # 4 MB history window
WMASK = (1 << WIN_BITS) - 1
HASH_BITS = 18
MAX_STRIDE = 2048


class Models:
    def __init__(self, stride):
        self.k = stride
        self.win = bytearray(1 << WIN_BITS)
        self.i = 0
        self.o1 = bytearray(256)
        self.o2 = bytearray(65536)
        self.match = [-1] * (1 << HASH_BITS)
        self.last_win = [1] * stride                       # per byte-position
        self.p_pick = [PROB_INIT] * (stride * 8 * 8)       # (lane, last winner) -> who wins
        self.p_err = [PROB_INIT] * (stride * 256)          # (lane) -> escape error
        self.wins = [0] * 8                                # stats

    def guess(self):
        w, i, k = self.win, self.i, self.k
        b1 = w[(i - 1) & WMASK]
        b2 = w[(i - 2) & WMASK]
        b3 = w[(i - 3) & WMASK]
        b4 = w[(i - 4) & WMASK]
        bk = w[(i - k) & WMASK]
        b2k = w[(i - 2 * k) & WMASK]
        h = (((b4 << 24) | (b3 << 16) | (b2 << 8) | b1) * 2654435761 & MASK32) >> (32 - HASH_BITS)
        mp = self.match[h]
        g = (
            b1,                                            # RLE
            bk,                                            # RECORD
            (2 * bk - b2k) & 255,                          # RECORD-TREND
            (2 * b1 - b2) & 255,                           # BYTE-TREND
            w[mp & WMASK] if mp >= 0 and i - mp <= WMASK else -1,   # MATCH
            self.o2[(b2 << 8) | b1],                       # ORDER-2
            self.o1[b1],                                   # ORDER-1
        )
        return g, h, b1, b2, bk

    def commit(self, c, h, b1, b2, lane, win):
        i = self.i
        self.win[i & WMASK] = c
        self.o1[b1] = c
        self.o2[(b2 << 8) | b1] = c
        self.match[h] = i
        self.last_win[lane] = win
        self.wins[win] += 1
        self.i = i + 1


def encode_byte(m, enc, c):
    g, h, b1, b2, bk = m.guess()
    lane = m.i % m.k
    lw = m.last_win[lane]
    # Pick a winner. If last time's winner (for this byte position) is right
    # again, keep it - it's the cheapest thing to write.
    if lw < ESC and g[lw] == c:
        win = lw
    else:
        win = ESC
        for j in range(ESC):
            if g[j] == c:
                win = j
                break
    put_tree(enc, m.p_pick, (lane * 8 + lw) * 8, 3, win)
    if win == ESC:
        put_tree(enc, m.p_err, lane * 256, 8, (c - bk) & 255)
    m.commit(c, h, b1, b2, lane, win)
    return win, g, bk


def decode_byte(m, dec):
    g, h, b1, b2, bk = m.guess()
    lane = m.i % m.k
    lw = m.last_win[lane]
    win = get_tree(dec, m.p_pick, (lane * 8 + lw) * 8, 3)
    if win == ESC:
        c = (bk + get_tree(dec, m.p_err, lane * 256, 8)) & 255
    else:
        c = g[win]
    m.commit(c, h, b1, b2, lane, win)
    return c, win, g, bk


def detect_stride(sample: bytes) -> int:
    """Find the record size: the shift where bytes most often equal the byte k back."""
    n = len(sample)
    best_k, scores = 1, {}
    for k in range(1, min(MAX_STRIDE, n // 4) + 1):
        x = int.from_bytes(sample[k:], "big") ^ int.from_bytes(sample[:-k], "big")
        scores[k] = x.to_bytes(n - k, "big").count(0) / (n - k)
    if not scores:
        return 1
    top = max(scores.values())
    for k in sorted(scores):                    # smallest k that's ~as good as the best
        if scores[k] >= 0.95 * top:
            best_k = k
            break
    return best_k


# =====================================================================
#  File-level compress / decompress (streaming)
# =====================================================================

CHUNK = 1 << 20


SHORT = ["RLE", "REC", "RTR", "BTR", "MAT", "O2", "O1"]


def trace_line(pos, c, win, g, bk):
    """One line of the play-by-play: which model won this byte, and what everyone guessed."""
    ch = chr(c) if 32 <= c < 127 else "."
    if win == ESC:
        err = (c - bk) & 255
        err = err - 256 if err > 127 else err
        who = f"ESCAPE (off by {err:+d} from RECORD)"
    else:
        who = NAMES[win]
    guesses = " ".join(
        f"{SHORT[j]}={'--' if x < 0 else f'{x:02X}'}{'*' if x == c else ' '}"
        for j, x in enumerate(g))
    return f"{pos:>10}  {c:02X} '{ch}'  {who:<32} {guesses}\n"


def write_trace_header(trace, stride):
    trace.write(f"HSC {VERSION} trace - record stride {stride} bytes\n")
    trace.write("guesses marked * were right; the winner is the one that got written\n\n")
    trace.write(f"{'offset':>10}  byte   {'winner':<32} guesses\n")


def compress_file(fin, fout, size, trace=None, trace_limit=None):
    first = fin.read(CHUNK)
    stride = detect_stride(first[: 1 << 17])
    fout.write(MAGIC + bytes([V2]) + struct.pack(">HQ", stride, size))
    m = Models(stride)
    enc = Encoder(fout)
    if trace:
        write_trace_header(trace, stride)
    pos = 0
    chunk = first
    while chunk:
        for c in chunk:
            win, g, bk = encode_byte(m, enc, c)
            if trace and (trace_limit is None or pos < trace_limit):
                trace.write(trace_line(pos, c, win, g, bk))
            pos += 1
        chunk = fin.read(CHUNK)
    enc.finish()
    return m


def store_file(fin, fout, size):
    """Fallback for data that won't compress (video, zip, encrypted): stride 0 = stored raw."""
    fout.write(MAGIC + bytes([V2]) + struct.pack(">HQ", 0, size))
    while chunk := fin.read(CHUNK):
        fout.write(chunk)


def decompress_file(fin, fout, trace=None, trace_limit=None):
    stride, n = struct.unpack(">HQ", fin.read(10))
    if stride == 0:                                  # stored raw
        if trace:
            trace.write("file was stored raw (it didn't compress) - no models ran\n")
        while chunk := fin.read(CHUNK):
            fout.write(chunk)
        return None
    m = Models(stride)
    dec = Decoder(fin)
    if trace:
        write_trace_header(trace, stride)
    out = bytearray()
    for pos in range(n):
        c, win, g, bk = decode_byte(m, dec)
        out.append(c)
        if trace and (trace_limit is None or pos < trace_limit):
            trace.write(trace_line(pos, c, win, g, bk))
        if len(out) >= CHUNK:
            fout.write(out)
            out.clear()
    fout.write(out)
    return m


# =====================================================================
#  Command line
# =====================================================================

HELP = f"""\
HSC {VERSION} - a compressor where seven models guess every byte
and only the winner gets written. Built for sensor data.

Usage:
  hsc c <file> [output]      compress    (default output: <file>.hsc)
  hsc d <file.hsc> [output]  decompress  (default output: name without .hsc)

Options:
  --trace [N]     also write <input>.trace.txt showing which model won
                  each byte (only the first N bytes if N is given)
  -h, --help      show this help
  -v, --version   show the version

Examples:
  hsc c scan.bin                  -> scan.bin.hsc
  hsc d scan.bin.hsc              -> scan.bin
  hsc c scan.bin --trace 5000     compress + log the first 5,000 bytes
  hsc d old.hsc restored.bin      also opens HSC 1.0 files

The models: RLE, RECORD, RECORD-TREND, BYTE-TREND, MATCH, ORDER-2, ORDER-1.
Data that won't compress (video, zip, encrypted) is stored raw (+14 bytes)."""


def usage_error(msg):
    print(f"hsc: {msg}", file=sys.stderr)
    print("Try 'hsc --help' for usage.", file=sys.stderr)
    return 2


def default_output(mode, src):
    if mode == "c":
        return src + EXT
    return src[: -len(EXT)] if src.endswith(EXT) else src + ".out"


def main(argv):
    # --trace [N]: write a play-by-play of who won each byte to <input>.trace.txt
    trace_on, trace_limit = False, None
    if "--trace" in argv:
        i = argv.index("--trace")
        trace_on = True
        if i + 1 < len(argv) and argv[i + 1].isdigit():
            trace_limit = int(argv[i + 1])
            del argv[i + 1]
        del argv[i]

    if not argv or any(a in ("-h", "--help", "/?") for a in argv):
        print(HELP)
        return 0
    if argv[0] in ("-v", "--version"):
        print(f"HSC {VERSION}")
        return 0
    unknown = [a for a in argv if a.startswith("--")]
    if unknown:
        return usage_error(f"unknown option '{unknown[0]}'")
    if argv[0] not in ("c", "d"):
        # Common slip: `hsc file.sav` with no c/d. Suggest the right one.
        guess = "d" if argv[0].endswith(EXT) else "c"
        word = "decompress" if guess == "d" else "compress"
        return usage_error(f"missing c or d - to {word} it, run: hsc {guess} {' '.join(argv)}")
    if len(argv) == 1:
        return usage_error(f"no file given - e.g. hsc {argv[0]} myfile")
    if len(argv) > 3:
        return usage_error("too many arguments (put quotes around names with spaces)")

    mode, src = argv[0], argv[1]
    dst = argv[2] if len(argv) == 3 else default_output(mode, src)
    if os.path.abspath(src) == os.path.abspath(dst):
        print("error: input and output are the same file", file=sys.stderr)
        return 1

    if not os.path.isfile(src):
        print(f"error: can't find '{src}' (check the name, and that you're in the right folder)",
              file=sys.stderr)
        return 1
    size_in = os.path.getsize(src)

    # Check the header BEFORE creating the output file, so a bad input
    # doesn't leave an empty output file lying around.
    if mode == "d":
        with open(src, "rb") as f:
            head = f.read(4)
        if len(head) < 4 or head[:3] != MAGIC:
            print("error: not an .hsc file (bad magic bytes)", file=sys.stderr)
            return 1
        if head[3] >> 4 not in (1, 2):
            v = head[3]
            print(f"error: unsupported .hsc version {v >> 4}.{v & 0xF}", file=sys.stderr)
            return 1

    trace_path = src + ".trace.txt"
    trace = open(trace_path, "w", encoding="utf-8") if trace_on else None
    try:
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            if mode == "c":
                m = compress_file(fin, fout, size_in, trace, trace_limit)
                if fout.tell() > size_in + 14:          # made it bigger -> store raw instead
                    fin.seek(0)
                    fout.seek(0)
                    fout.truncate()
                    store_file(fin, fout, size_in)
                    m = None
                    print("  (data doesn't compress - stored raw)")
            else:
                head = fin.read(4)
                if head[3] >> 4 == 1:                   # old HSC 1.0 file
                    if trace:
                        trace.write("HSC 1.0 file (Huffman only) - no models to trace\n")
                    fout.write(decompress_v1(head + fin.read()))
                    m = None
                else:
                    m = decompress_file(fin, fout, trace, trace_limit)
    finally:
        if trace:
            trace.close()
            print(f"  trace written to {trace_path}")
    size_out = os.path.getsize(dst)

    msg = f"HSC {VERSION}: {src} -> {dst}  ({size_in:,} -> {size_out:,} bytes"
    if mode == "c" and size_in:
        msg += f", {size_out / size_in * 100:.1f}% of original"
    print(msg + ")")
    if mode == "c" and m and m.i:
        print(f"  record stride: {m.k} bytes")
        print("  winners: " + ", ".join(
            f"{name} {m.wins[j] / m.i * 100:.0f}%"
            for j, name in enumerate(NAMES + ["none (escape)"]) if m.wins[j]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
