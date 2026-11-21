# HSC

A Huffman file compressor. It gives common bytes short codes and rare bytes
long codes, and writes `.hsc` files.

```
python3 hsc.py c notes.txt          # -> notes.txt.hsc
python3 hsc.py d notes.txt.hsc      # -> notes.txt
python3 hsc.py --version
```

English text typically shrinks to about 50–55% of its size.

## File format (v1.0)

| Bytes | Field |
|---|---|
| 3 | magic `HSC` |
| 1 | version `0x10` |
| 4 | original length, big-endian |
| 1024 | byte frequency table (256 × 4 bytes) |
| … | packed Huffman bitstream |

## License

[GPL-3.0-or-later](LICENSE)
