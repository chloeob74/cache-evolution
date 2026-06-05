"""Evaluate cache replacement policies on trace file."""

from cache_simulator import parse_trace, LRUCache, LFUCache, FIFOCache


TRACE_FILE = "msr_hm_0.oracleGeneral.zst"
CACHE_SIZE_BYTES = 64 * 1024 * 1024  # 64MB


def run_policy(cache_class, records):
    """Run a cache policy against all records and return hit rate."""
    cache = cache_class(CACHE_SIZE_BYTES)
    for timestamp, obj_id, obj_size, next_access in records:
        cache.access(obj_id, obj_size)
    return cache.hit_rate()


def main():
    records = parse_trace(TRACE_FILE)
    
    lru_rate = run_policy(LRUCache, records)
    lfu_rate = run_policy(LFUCache, records)
    fifo_rate = run_policy(FIFOCache, records)
    
    print(f"LRU: {lru_rate:.2f}, LFU: {lfu_rate:.2f}, FIFO: {fifo_rate:.2f}")


if __name__ == "__main__":
    main()
