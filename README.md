# Evolving Cache Replacement Policies with OpenEvolve

Can a language model *discover* a better cache eviction policy on its own? This project uses [OpenEvolve](https://github.com/codelion/openevolve) — an open-source implementation of DeepMind's AlphaEvolve — to evolve a byte-based cache replacement policy, starting from a plain LRU seed and improving it against a real-world storage trace.

The search rediscovered a **Greedy-Dual-Size-Frequency (GDSF)** policy that lifts the hit rate from LRU's **47.9%** to **53.1%** — closing **71%** of the gap to the clairvoyant Belady/MIN optimum.

| Policy | Hit rate | Notes |
|---|---:|---|
| LRU (seed) | 47.93% | Starting point |
| LFU | 50.36% | Frequency baseline |
| **Evolved (GDSF)** | **53.11%** | Discovered by the search |
| Belady / MIN | 55.23% | Clairvoyant ceiling |

> [!NOTE]
> This is a research/coursework project, not a library. It evolves *one* policy for *one* workload to study how — and how well — an LLM-driven evolutionary loop searches an algorithm space.

## How it works

OpenEvolve runs an evolutionary loop where an LLM acts as the mutation operator:

1. **Seed** — start from a byte-based LRU policy (`initial_program.py`). Only the code between `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` markers may change.
2. **Mutate** — Claude Sonnet 4.6 proposes a code edit, guided by `prompt.txt` (the O(1)-eviction constraint, known strategies, and scoring rules) and by high-scoring programs from the population.
3. **Evaluate** — `evaluate.py` replays a 50,000-request trace through the candidate and scores it on hit rate, with time bonuses/penalties.
4. **Select** — a MAP-Elites database keeps a diverse population across **3 islands** with periodic migration, so the search explores rather than collapsing onto one idea.

Repeat for 50 iterations; keep the best.

### Scoring

The score the LLM optimizes is *not* raw hit rate — it is shaped to encode an O(1) performance budget:

```
score = hit_rate × time_factor
```

| Condition | Effect |
|---|---|
| Runs < 2s | ×1.05 (bonus) |
| Runs 2–25s | ×1.00 |
| Runs > 25s | graduated penalty up to ×0.85 |
| Runs > 30s | timeout → score 0 |
| Hit rate < 47.90% | ×0.5 (heavy penalty) |

> [!IMPORTANT]
> The wall-clock budget is a *proxy* for the "eviction must be O(1)" constraint. A candidate that beats LRU's hit rate but iterates over the whole cache on eviction gets penalized into irrelevance — the reward function, not the prompt, is what actually enforces the design rules.

## Project layout

| File | Role |
|---|---|
| `initial_program.py` | LRU seed with the evolvable block |
| `evaluate.py` | Scorer — replays the trace, computes hit rate + time factor |
| `config.yaml` | OpenEvolve config (model, islands, population, iterations) |
| `prompt.txt` | Instructions to the LLM mutation operator |
| `run_evolution.sh` | Launches a run with the API key sourced safely |
| `cache_simulator.py` | Reference LRU / LFU / FIFO implementations |
| `compute_optimal.py` | Belady/MIN clairvoyant ceiling |
| `visualize.py` | Builds the interactive `evolution_report.html` |
| `msr_hm_0.oracleGeneral.zst` | The MSR Cambridge storage trace |
| `openevolve_output/best/best_program.py` | The winning evolved policy |
| `REPORT.md` | Full write-up: design, findings, and implications |

## Getting started

### Prerequisites

- Python ≥ 3.10
- An Anthropic API key
- Dependencies: `openevolve`, `zstandard`, `numpy`, `plotly`

### Set up the API key

The key is read per-run from a protected file so it never lives in your shell environment:

```bash
mkdir -p ~/.config/openevolve
printf '%s' 'sk-ant-...' > ~/.config/openevolve/anthropic_api_key
chmod 600 ~/.config/openevolve/anthropic_api_key
```

> [!WARNING]
> Keep `ANTHROPIC_API_KEY` out of your global environment. `run_evolution.sh` injects it for the single OpenEvolve command only — exporting it elsewhere can route billing to API credits instead of your subscription.

### Run an evolution

```bash
# Default: 50 iterations
./run_evolution.sh

# Or specify an iteration budget
./run_evolution.sh 100
```

Results land in `openevolve_db/` (the searched population) and `openevolve_output/best/` (the top program).

### Check the baselines and the ceiling

```bash
python compute_optimal.py    # prints LRU and Belady/MIN hit rates for the trace
```

### Build the report

```bash
python visualize.py          # writes evolution_report.html
```

Open `evolution_report.html` for an interactive dashboard: the hit-rate frontier, per-iteration trajectories against the LRU/LFU/MIN reference lines, the evolution tree, and per-island progress — plus a glossary of terms.

## The evolved policy

The winner is a **Greedy-Dual-Size-Frequency** cache. Each object gets a priority of `clock + frequency / size`, evicting the lowest-priority object via a lazy-deletion min-heap. Two details did most of the work:

- **Ghost frequency history** — frequency counts persist after eviction, so a returning object is recognized as valuable.
- **One-hit-wonder admission** — first-time objects are blocked from a full cache, protecting resident working-set items from churn.

Here is the exact code the search produced (the evolved block, replacing LRU's `__init__` and `access`):

```python
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
```

See [`REPORT.md`](REPORT.md) for the full analysis, including why the headroom of the workload decided success, where the search rediscovered known ideas rather than inventing new ones, and the implications for reward design in AI-driven systems work.

## Acknowledgments

- [OpenEvolve](https://github.com/codelion/openevolve) — the evolutionary coding framework.
- Workload trace from the [MSR Cambridge](https://www.usenix.org/conference/fast08) block I/O traces, in the `oracleGeneral` format used by [libCacheSim](https://github.com/1a1a11a/libCacheSim).
