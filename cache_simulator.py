"""Cache replacement policy simulator with LRU, LFU, and FIFO policies."""

import struct
from collections import OrderedDict
import zstandard as zstd


def parse_trace(filepath):
    """Parse binary trace file in oracleGeneral format."""
    record_format = "<IQIq"  # timestamp, obj_id, obj_size, next_access
    record_size = 24
    
    records = []
    dctx = zstd.ZstdDecompressor()
    
    with open(filepath, 'rb') as f:
        decompressed = dctx.decompress(f.read())
    
    num_records = len(decompressed) // record_size
    for i in range(num_records):
        offset = i * record_size
        timestamp, obj_id, obj_size, next_access = struct.unpack_from(
            record_format, decompressed, offset
        )
        records.append((timestamp, obj_id, obj_size, next_access))
    
    return records


class Cache:
    """Base cache class with byte-based capacity."""
    
    def __init__(self, capacity_bytes):
        self.capacity = capacity_bytes
        self.current_size = 0
        self.hits = 0
        self.misses = 0
    
    def access(self, obj_id, obj_size):
        """Process an access request. Returns True for hit, False for miss."""
        raise NotImplementedError
    
    def hit_rate(self):
        """Return the hit rate as a fraction."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
    
    def _evict_until_fits(self, needed_size):
        """Evict items until there's room for needed_size bytes."""
        raise NotImplementedError


class LRUCache(Cache):
    """Least Recently Used replacement policy."""
    
    def __init__(self, capacity_bytes):
        super().__init__(capacity_bytes)
        self.cache = OrderedDict()  # obj_id -> obj_size
    
    def access(self, obj_id, obj_size):
        if obj_id in self.cache:
            # Hit: move to end (most recently used)
            self.cache.move_to_end(obj_id)
            self.hits += 1
            return True
        
        # Miss
        self.misses += 1
        
        # Skip objects larger than cache capacity
        if obj_size > self.capacity:
            return False
        
        self._evict_until_fits(obj_size)
        self.cache[obj_id] = obj_size
        self.current_size += obj_size
        return False
    
    def _evict_until_fits(self, needed_size):
        while self.current_size + needed_size > self.capacity and self.cache:
            evicted_id, evicted_size = self.cache.popitem(last=False)
            self.current_size -= evicted_size


class LFUCache(Cache):
    """Least Frequently Used replacement policy."""
    
    def __init__(self, capacity_bytes):
        super().__init__(capacity_bytes)
        self.cache = {}  # obj_id -> obj_size
        self.freq = {}   # obj_id -> access count
        self.freq_to_keys = {}  # frequency -> set of obj_ids (with insertion order)
        self.min_freq = 0
    
    def access(self, obj_id, obj_size):
        if obj_id in self.cache:
            # Hit: increment frequency
            self._increment_freq(obj_id)
            self.hits += 1
            return True
        
        # Miss
        self.misses += 1
        
        if obj_size > self.capacity:
            return False
        
        self._evict_until_fits(obj_size)
        
        # Insert new item with frequency 1
        self.cache[obj_id] = obj_size
        self.freq[obj_id] = 1
        if 1 not in self.freq_to_keys:
            self.freq_to_keys[1] = OrderedDict()
        self.freq_to_keys[1][obj_id] = True
        self.min_freq = 1
        self.current_size += obj_size
        return False
    
    def _increment_freq(self, obj_id):
        """Increment the frequency of an object."""
        old_freq = self.freq[obj_id]
        new_freq = old_freq + 1
        self.freq[obj_id] = new_freq
        
        # Remove from old frequency set
        del self.freq_to_keys[old_freq][obj_id]
        if not self.freq_to_keys[old_freq]:
            del self.freq_to_keys[old_freq]
            if self.min_freq == old_freq:
                self.min_freq = new_freq
        
        # Add to new frequency set
        if new_freq not in self.freq_to_keys:
            self.freq_to_keys[new_freq] = OrderedDict()
        self.freq_to_keys[new_freq][obj_id] = True
    
    def _evict_until_fits(self, needed_size):
        while self.current_size + needed_size > self.capacity and self.cache:
            # Find and remove least frequent item (FIFO among ties)
            min_freq_keys = self.freq_to_keys[self.min_freq]
            evicted_id = next(iter(min_freq_keys))
            evicted_size = self.cache[evicted_id]
            
            del min_freq_keys[evicted_id]
            if not min_freq_keys:
                del self.freq_to_keys[self.min_freq]
                # min_freq will be reset on next insert
            
            del self.cache[evicted_id]
            del self.freq[evicted_id]
            self.current_size -= evicted_size
            
            # Update min_freq if needed
            if self.freq_to_keys:
                self.min_freq = min(self.freq_to_keys.keys())


class FIFOCache(Cache):
    """First In First Out replacement policy."""
    
    def __init__(self, capacity_bytes):
        super().__init__(capacity_bytes)
        self.cache = {}  # obj_id -> obj_size
        self.order = OrderedDict()  # obj_id -> True (insertion order)
    
    def access(self, obj_id, obj_size):
        if obj_id in self.cache:
            # Hit: do not change order (FIFO)
            self.hits += 1
            return True
        
        # Miss
        self.misses += 1
        
        if obj_size > self.capacity:
            return False
        
        self._evict_until_fits(obj_size)
        self.cache[obj_id] = obj_size
        self.order[obj_id] = True
        self.current_size += obj_size
        return False
    
    def _evict_until_fits(self, needed_size):
        while self.current_size + needed_size > self.capacity and self.cache:
            evicted_id = next(iter(self.order))
            evicted_size = self.cache[evicted_id]
            del self.order[evicted_id]
            del self.cache[evicted_id]
            self.current_size -= evicted_size
