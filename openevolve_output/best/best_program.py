"""Evolvable cache replacement policy starting from LRU baseline."""

from collections import OrderedDict


class EvolvedCache:
    """Byte-based cache with evolvable replacement policy."""
    
    def __init__(self, capacity_bytes):
        self.capacity = capacity_bytes
        self.current_size = 0
        self.hits = 0
        self.misses = 0
        self.cache = OrderedDict()
    
    def access(self, obj_id, obj_size):
        """Process an access request. Returns True for hit, False for miss."""
        # EVOLVE-START
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
        
        # Evict until there's room (FIFO/LRU)
        while self.current_size + obj_size > self.capacity and self.cache:
            evicted_id, evicted_size = self.cache.popitem(last=False)
            self.current_size -= evicted_size
        
        # Insert new object
        self.cache[obj_id] = obj_size
        self.current_size += obj_size
        return False
        # EVOLVE-END
    
    def hit_rate(self):
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
