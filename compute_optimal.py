"""Compute Belady/MIN clairvoyant hit rate for the trace, matching evaluate.py setup.

True optimal for a variable-size (byte) cache is NP-hard; this computes the standard
Belady reference (evict farthest-next-use; don't evict sooner-needed items to admit a
later-needed newcomer) as a headroom indicator vs LRU.
"""
import struct
import time
import heapq
from collections import OrderedDict
import zstandard as zstd

TRACE_FILE = "msr_hm_0.oracleGeneral.zst"
CAPACITY = 64 * 1024 * 1024
N = 50000
INF = float("inf")


def load_records():
    dctx = zstd.ZstdDecompressor()
    with open(TRACE_FILE, "rb") as f:
        data = dctx.decompress(f.read())
    recs = [
        (oid, sz)
        for ts, oid, sz, na in struct.iter_unpack("<IQIq", memoryview(data))
    ]
    return recs[:N]


def lru_hit_rate(recs):
    """Sanity check: must reproduce the evaluator's 0.5670 baseline."""
    cache = OrderedDict()
    size = 0
    hits = 0
    for oid, sz in recs:
        if oid in cache:
            cache.move_to_end(oid)
            hits += 1
            continue
        if sz > CAPACITY:
            continue
        while size + sz > CAPACITY and cache:
            _, esz = cache.popitem(last=False)
            size -= esz
        cache[oid] = sz
        size += sz
    return hits / len(recs)


def belady_hit_rate(recs):
    n = len(recs)
    # next_use[i] = index of next occurrence of recs[i] obj, or INF
    next_use = [INF] * n
    last = {}
    for i in range(n - 1, -1, -1):
        oid = recs[i][0]
        next_use[i] = last.get(oid, INF)
        last[oid] = i

    cache = {}          # oid -> size
    cur_nu = {}         # oid -> its current next-use index
    heap = []           # max-heap on next-use: entries (-next_use, oid)
    size = 0
    hits = 0

    def push(oid, nu):
        heapq.heappush(heap, (-nu, oid))

    def peek_max():
        # return (nu, oid) of farthest-next-use live cached item, cleaning stale entries
        while heap:
            neg, eid = heap[0]
            nu = -neg
            if eid in cache and cur_nu.get(eid) == nu:
                return nu, eid
            heapq.heappop(heap)
        return None

    for i, (oid, sz) in enumerate(recs):
        nu = next_use[i]
        if oid in cache:
            hits += 1
            cur_nu[oid] = nu
            push(oid, nu)
            continue
        if sz > CAPACITY:
            continue
        if nu == INF:
            continue  # never used again -> admitting can't yield a hit
        # Make room, but only by evicting items needed LATER than this one.
        while size + sz > CAPACITY:
            m = peek_max()
            if m is None or m[0] <= nu:
                break  # newcomer is the farthest-needed -> don't thrash
            _, eid = m
            heapq.heappop(heap)
            size -= cache.pop(eid)
            cur_nu.pop(eid, None)
        if size + sz <= CAPACITY:
            cache[oid] = sz
            size += sz
            cur_nu[oid] = nu
            push(oid, nu)
    return hits / n


if __name__ == "__main__":
    recs = load_records()
    print(f"records: {len(recs)}  cache: {CAPACITY // (1024*1024)}MB")

    t = time.time()
    lru = lru_hit_rate(recs)
    print(f"LRU  hit_rate = {lru:.4f}   (expect ~0.5670)  [{time.time()-t:.1f}s]")

    t = time.time()
    opt = belady_hit_rate(recs)
    print(f"MIN  hit_rate = {opt:.4f}   (Belady clairvoyant reference)  [{time.time()-t:.1f}s]")

    print(f"\nHeadroom above LRU: {opt - lru:+.4f}  ({(opt-lru)*100:.2f} pp)")
    print(f"Best evolved policy was 0.5716 -> closes "
          f"{(0.5716 - lru) / (opt - lru) * 100:.1f}% of the LRU->MIN gap" if opt > lru else "")
