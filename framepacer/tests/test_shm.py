"""Leitor do protocolo .shm v1/v2 (sem Qt, determinístico)."""
import os
import struct
import sys
import tempfile

sys.path.insert(0, ".")
import framepacer as f

TMP = tempfile.mkdtemp(prefix="fp-shm-")


def write_shm(pid, version, limit=60, head=0, samples=()):
    path = os.path.join(TMP, f"{pid}.shm")
    ring = [0] * f.FT_N
    for i, v in enumerate(samples):
        ring[i % f.FT_N] = min(v, 65535)
    with open(path, "wb") as fh:
        fh.write(struct.pack(f.FMT, f.MAGIC, version, pid, 0, limit, 6000, 1, b"fakegame"))
        if version >= 2:
            fh.write(struct.pack("<II", head, min(len(samples), f.FT_N)))
            fh.write(struct.pack("<HH", 0, 0))
            fh.write(struct.pack(f"<{f.FT_N}H", *ring))
            fh.write(b"\0" * (f.FT_SIZE - fh.tell()))
    return path


# v1 (64 bytes): sem gráfico
g1 = f.Game(write_shm(42421, 1), 42421)
assert g1.frame_times_ms() == [], g1.frame_times_ms()
print("v1 sem ring OK")

# v2 sem wrap: head=5, count=5 -> ordem de escrita
g2 = f.Game(write_shm(42422, 2, head=5, samples=[10000, 11000, 12000, 13000, 14000]), 42422)
assert g2.frame_times_ms() == [10.0, 11.0, 12.0, 13.0, 14.0], g2.frame_times_ms()
print("v2 ordenado OK")

# v2 com wrap: 125 amostras, head=5, count=120 -> raw[5..119] + raw[0..4]
samples = [8000 + i for i in range(125)]
g3 = f.Game(write_shm(42423, 2, head=125 % f.FT_N, samples=samples), 42423)
got = g3.frame_times_ms()
assert len(got) == f.FT_N, len(got)
want = [(8000 + i) / 1000.0 for i in range(5, 125)]
assert got == want, (got[:3], want[:3])
print("v2 wrap OK")

print("SHM TESTS OK")
