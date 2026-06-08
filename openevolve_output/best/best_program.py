"""Evolvable cache replacement policy starting from LRU baseline."""

from collections import OrderedDict
import heapq


class EvolvedCache:
    """Byte-based cache with evolvable replacement policy."""
    
    # EVOLVE-BLOCK-START
    def __init__(self, capacity_bytes):
        self.capacity = capacity_bytes
        self.current_size = 0
        self.hits = 0
        self.misses = 0
        self.cache = {}  # obj_id -> (obj_size, freq, priority)
        self.freq = {}   # obj_id -> access count (persists across evictions)
        self.clock = 0   # GDSF clock value
        self.heap = []   # min-heap of (priority, obj_id)

    def access(self, obj_id, obj_size):
        """Process an access request. Returns True for hit, False for miss."""
        if obj_id in self.cache:
            size, f, _ = self.cache[obj_id]
            new_f = min(f + 1, 31)
            self.freq[obj_id] = new_f
            new_priority = self.clock + new_f / size
            self.cache[obj_id] = (size, new_f, new_priority)
            heapq.heappush(self.heap, (new_priority, obj_id))
            self.hits += 1
            return True

        # Miss
        self.misses += 1
        f = self.freq.get(obj_id, 0) + 1
        self.freq[obj_id] = f

        if obj_size > self.capacity:
            return False

        # Admission: block first-time objects when cache is full
        if f == 1 and self.current_size + obj_size > self.capacity:
            return False

        # Evict using heap (lazy deletion for stale entries)
        while self.current_size + obj_size > self.capacity and self.cache:
            while self.heap:
                pri, eid = heapq.heappop(self.heap)
                if eid in self.cache and self.cache[eid][2] == pri:
                    # Valid eviction candidate
                    self.clock = pri
                    esz, _, _ = self.cache.pop(eid)
                    self.current_size -= esz
                    break
            else:
                # Heap empty but cache not - fallback
                eid = next(iter(self.cache))
                esz, _, _ = self.cache.pop(eid)
                self.current_size -= esz

        priority = self.clock + f / obj_size
        self.cache[obj_id] = (obj_size, f, priority)
        heapq.heappush(self.heap, (priority, obj_id))
        self.current_size += obj_size
        return False
    # EVOLVE-BLOCK-END
    
    def hit_rate(self):
        """Return the hit rate as a fraction."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
