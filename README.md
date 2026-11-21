# HSC

**A compressor where seven models guess every byte, and only the winner gets written.**

HSC is built for sensor data: lidar scans, IMU logs, anything made of
fixed-size records. Each model tries to predict the next byte. If one is
right, HSC writes down *which model won*, which usually costs 1–2 bits instead
of 8. If none is right, it writes how far off the best guess was. For noisy
sensor readings that number is small, so it's cheap too.

```
$ hsc c scan.bin
HSC 2.0: scan.bin -> scan.bin.hsc  (2,144,000 -> 608,394 bytes, 28.4% of original)
  record stride: 268 bytes
  winners: RLE 5%, RECORD 56%, RECORD-TREND 3%, MATCH 4%, ORDER-2 1%, none (escape) 31%
```

## Results

| File | Original | gzip -9 | bzip2 -9 | xz -9 | **HSC 2.0** |
|---|---|---|---|---|---|
| Lidar-style records* | 2,144,000 | 1,095,783 | 821,694 | 714,340 | **608,394** |
| Garry's Mod save (`.sav`) | 233,785 | | | | **68,603** |
| English-like text | 915,807 | 136,870 | **88,600** | 114,960 | 220,491 |
| Random data | 300,000 | 300,079 | 301,781 | 300,072 | **300,014** (stored raw) |

\*Synthetic Ouster-style lidar records with realistic sensor noise. I'm still
collecting real-world benchmarks.

HSC beats gzip, bzip2 and xz on record-structured data. It loses on plain
text, because general-purpose compressors are better at long repeated
phrases.

## How it works

For every byte, seven models each make a guess:

| # | Model | Guesses... |
|---|---|---|
| 0 | RLE | the same as the previous byte |
| 1 | RECORD | the same as this byte one record ago |
| 2 | RECORD-TREND | that the trend across records continues (delta) |
| 3 | BYTE-TREND | that the byte-to-byte trend continues |
| 4 | MATCH | "this 4-byte pattern happened before; what came next then?" |
| 5 | ORDER-2 | whatever last followed these 2 bytes |
| 6 | ORDER-1 | whatever last followed this byte |

- **Only the winner is written.** An adaptive binary range coder (the same
  kind LZMA uses) learns which model tends to win at each byte position, so
  a predictable winner costs a fraction of a bit.
- **The decoder replays the encoder.** It runs the same models, so
  "model 1 won" is enough for it to rebuild the byte.
- **The record size is auto-detected,** and each byte position within a
  record gets its own statistics.
- **Files are streamed,** so memory stays around 10 MB no matter how big
  the file is.
- **Data that won't compress** (video, zip, encrypted) is stored raw, with
  14 bytes of overhead.

Want to watch it think? `--trace` logs the winner and every guess for each byte:

```
    offset  byte   winner                           guesses
      2704  3E '>'  ESCAPE (off by +4 from RECORD)   RLE=00  REC=3A  RTR=36  BTR=00  MAT=--  O2=36  O1=00
      2705  00 '.'  RECORD                           RLE=3E  REC=00* RTR=00* BTR=7C  MAT=00* O2=00* O1=00*
      2706  26 '&'  MATCH                            RLE=00  REC=37  RTR=41  BTR=C2  MAT=26* O2=26* O1=3E
```

## Install

All installers need **Python 3.8+**.

**Windows:** download the repo (Code → Download ZIP), open `windows`, and
double-click **install.cmd**. It installs for your user only, no admin needed,
and adds right-click **Compress with HSC** / **Extract with HSC**.
`uninstall.cmd` removes it.

**Linux / macOS:**
```
sh install.sh                        # system-wide (asks for sudo)
PREFIX=$HOME/.local sh install.sh    # just for you
sh install.sh --uninstall
```
On Linux, the installer also teaches `file` to recognize `.hsc` files.

Or skip installing and run `python3 hsc.py` directly.

## Usage

```
hsc c <file> [output]       compress     (default: <file>.hsc)
hsc d <file.hsc> [output]   decompress   (default: name without .hsc)
hsc c <file> --trace [N]    also write <file>.trace.txt (first N bytes)
hsc --help
```

## File format (v2.0)

| Bytes | Field |
|---|---|
| 3 | magic `HSC` |
| 1 | version `0x20` (high nibble = major, low = minor) |
| 2 | record stride, big-endian (`0` = stored raw) |
| 8 | original length, big-endian |
| … | range-coded stream |

HSC 2.0 also reads HSC 1.0 files (Huffman only, version byte `0x10`).

## Roadmap

- [ ] Multi-byte match model (big win on text and game saves)
- [ ] Benchmarks on real lidar, IMU and camera data
- [ ] C or Rust port (Python runs at about 200 KB/s)
- [ ] Standalone builds that don't need Python

## Testing

```
python -m unittest discover -v tests
```
CI runs the tests and all the installers on Windows, macOS and Linux.

## License

[GPL-3.0-or-later](LICENSE). You're free to use, change and share HSC. If you
distribute a modified version, you have to share its source under the same
license.
