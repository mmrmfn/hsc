"""Round-trip tests for HSC. Run with:  python -m unittest discover tests"""
import os
import random
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HSC = os.path.join(HERE, "..", "hsc.py")


def run(*args):
    return subprocess.run([sys.executable, HSC, *args], capture_output=True, text=True)


def sensor_records(n=1500):
    """Fixed-size records like a lidar/IMU log: timestamp, counter, noisy readings."""
    rnd = random.Random(1)
    out, t = bytearray(), 1_000_000
    for i in range(n):
        t += 1000 + rnd.randint(-2, 2)
        out += struct.pack("<QH", t, i & 0xFFFF)
        for ch in range(8):
            out += struct.pack("<IH", 5000 + ch * 300 + rnd.randint(-20, 20), 40 + rnd.randint(-3, 3))
    return bytes(out)


SAMPLES = {
    "empty": b"",
    "one_byte": b"x",
    "repeated": b"A" * 5000,
    "text": b" ".join(random.Random(2).choice([b"map", b"lidar", b"truck", b"street"])
                      for _ in range(4000)),
    "random": random.Random(3).randbytes(20000),
    "sensor": sensor_records(),
}


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)

    def roundtrip(self, name, data):
        src = self.path(name)
        with open(src, "wb") as f:
            f.write(data)
        r = run("c", src)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run("d", src + ".hsc", src + ".back")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(src + ".back", "rb") as f:
            self.assertEqual(f.read(), data, f"{name} did not round-trip")
        return os.path.getsize(src + ".hsc")

    def test_all_samples(self):
        for name, data in SAMPLES.items():
            with self.subTest(name):
                self.roundtrip(name, data)

    def test_sensor_data_compresses_well(self):
        size = self.roundtrip("sensor", SAMPLES["sensor"])
        self.assertLess(size, len(SAMPLES["sensor"]) * 0.5)

    def test_random_data_never_grows_much(self):
        size = self.roundtrip("random", SAMPLES["random"])
        self.assertLessEqual(size, len(SAMPLES["random"]) + 14)

    def test_header(self):
        self.roundtrip("sensor", SAMPLES["sensor"])
        with open(self.path("sensor.hsc"), "rb") as f:
            self.assertEqual(f.read(4), b"HSC\x20")

    def test_trace_matches_between_compress_and_decompress(self):
        src = self.path("s.bin")
        with open(src, "wb") as f:
            f.write(SAMPLES["sensor"][:4000])
        run("c", src, "--trace", "2000")
        with open(src + ".trace.txt") as f:
            c_trace = f.read()
        run("d", src + ".hsc", self.path("s.back"), "--trace", "2000")
        with open(src + ".hsc.trace.txt") as f:
            d_trace = f.read()
        self.assertEqual(c_trace, d_trace)
        self.assertIn("RECORD", c_trace)

    def test_bad_input(self):
        junk = self.path("junk.hsc")
        with open(junk, "wb") as f:
            f.write(b"not an hsc file")
        self.assertNotEqual(run("d", junk, self.path("out")).returncode, 0)
        self.assertFalse(os.path.exists(self.path("out")))
        self.assertNotEqual(run("d", self.path("missing.hsc")).returncode, 0)

    def test_help_and_version(self):
        self.assertIn("Usage", run("--help").stdout)
        self.assertIn("Usage", run("-h").stdout)
        self.assertIn("HSC", run("--version").stdout)


if __name__ == "__main__":
    unittest.main()
