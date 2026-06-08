"""Evaluator for OpenEvolve evolutionary search."""

import struct
import importlib.util
import time
import zstandard as zstd


TRACE_FILE = "msr_hm_0.oracleGeneral.zst"
CACHE_SIZE_BYTES = 16 * 1024 * 1024  # 16MB
MAX_RECORDS = 50000  # Sample size for faster evaluation
EVAL_TIMEOUT = 30  # Seconds before evaluation is aborted
TIMEOUT_CHECK_INTERVAL = 10000  # Check timeout every N records
FAST_THRESHOLD = 2.0  # Seconds - faster than this gets bonus
SLOW_THRESHOLD = 25.0  # Seconds - only penalize very slow execution
BASELINE_HIT_RATE = 0.4790  # just below LRU seed (0.47926) at 16MB / 50k so LRU isn't self-penalized (LFU=0.5036, MIN=0.5523)

_cached_records = None


def _parse_trace():
    """Parse binary trace file in oracleGeneral format using batch unpacking."""
    global _cached_records
    if _cached_records is not None:
        return _cached_records
    
    record_format = "<IQIq"
    record_size = 24
    
    dctx = zstd.ZstdDecompressor()
    with open(TRACE_FILE, 'rb') as f:
        decompressed = dctx.decompress(f.read())
    
    # Batch unpack all records at once using struct.iter_unpack on memoryview
    mv = memoryview(decompressed)
    records = [
        (obj_id, obj_size)
        for timestamp, obj_id, obj_size, next_access in struct.iter_unpack(record_format, mv)
    ]
    
    _cached_records = records
    return records


def _load_cache_class(program_path):
    """Dynamically load EvolvedCache from program path."""
    spec = importlib.util.spec_from_file_location("evolved_module", program_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EvolvedCache


def _get_cache_stats(cache):
    """Extract debugging statistics from cache object."""
    stats = {
        "final_size_bytes": getattr(cache, 'current_size', 0),
        "final_size_mb": round(getattr(cache, 'current_size', 0) / (1024 * 1024), 2),
        "capacity_mb": round(getattr(cache, 'capacity', 0) / (1024 * 1024), 2),
        "total_hits": getattr(cache, 'hits', 0),
        "total_misses": getattr(cache, 'misses', 0),
    }
    
    # Count items in various cache structures
    if hasattr(cache, 'cache'):
        stats["cache_items"] = len(cache.cache)
    if hasattr(cache, 'probation'):
        stats["probation_items"] = len(cache.probation)
    if hasattr(cache, 'protected'):
        stats["protected_items"] = len(cache.protected)
    if hasattr(cache, 'protected_size'):
        stats["protected_size_mb"] = round(cache.protected_size / (1024 * 1024), 2)
    if hasattr(cache, 'freq_buckets'):
        stats["freq_bucket_counts"] = {k: len(v) for k, v in cache.freq_buckets.items()}
    
    return stats


def evaluate(program_path: str) -> dict:
    """Evaluate the evolved cache program and return score."""
    try:
        records = _parse_trace()[:MAX_RECORDS]
        cache_class = _load_cache_class(program_path)
        cache = cache_class(CACHE_SIZE_BYTES)
        
        start_time = time.time()
        checks_remaining = 0
        for i, (obj_id, obj_size) in enumerate(records):
            if checks_remaining == 0:
                if time.time() - start_time > EVAL_TIMEOUT:
                    return {
                        "score": 0.0,
                        "hit_rate": 0.0,
                        "time": EVAL_TIMEOUT,
                        "combined_score": 0.0,
                        "artifacts": {"error": "timeout", "processed_records": i}
                    }
                checks_remaining = TIMEOUT_CHECK_INTERVAL
            checks_remaining -= 1
            cache.access(obj_id, obj_size)
        
        elapsed = time.time() - start_time
        hit_rate = cache.hit_rate()
        
        # Apply graduated time-based adjustment to score
        if elapsed < FAST_THRESHOLD:
            time_factor = 1.05
        elif elapsed > SLOW_THRESHOLD:
            penalty = min(0.15, (elapsed - SLOW_THRESHOLD) * 0.02)
            time_factor = 1.0 - penalty
        else:
            time_factor = 1.0
        
        # Heavy penalty for programs below baseline hit rate
        if hit_rate < BASELINE_HIT_RATE:
            time_factor *= 0.5
        
        adjusted_score = hit_rate * time_factor
        
        # Collect cache statistics as artifacts for debugging
        artifacts = _get_cache_stats(cache)
        artifacts["time_factor"] = round(time_factor, 3)
        
        return {
            "score": adjusted_score,
            "hit_rate": hit_rate,
            "time": elapsed,
            "combined_score": adjusted_score,
            "artifacts": artifacts
        }
    
    except Exception as e:
        return {
            "score": 0.0,
            "hit_rate": 0.0,
            "time": 0.0,
            "combined_score": 0.0,
            "artifacts": {"error": str(e)}
        }
