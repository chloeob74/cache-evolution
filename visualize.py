"""Standalone visualization script for OpenEvolve cache-policy results.

Reads the program database and log files, computes human/optimal baselines,
and generates a single interactive HTML report with a background glossary,
context KPIs, and report-ready charts.

Usage:
    python visualize.py [--db-dir ./openevolve_db] \
                        [--log-dir openevolve_output/logs] \
                        [--output evolution_report.html]
"""

import argparse
import json
import os
import glob
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots


# Must match evaluate.py so the baselines are computed on the same workload.
TRACE_FILE = "msr_hm_0.oracleGeneral.zst"
CACHE_SIZE_BYTES = 16 * 1024 * 1024  # 16MB
MAX_RECORDS = 50000
FAST_THRESHOLD = 2.0   # eval faster than this earns the 1.05 score bonus
SLOW_THRESHOLD = 25.0  # eval slower than this is graduated-penalized
TIMEOUT = 30.0
BASELINE_CACHE = "baselines_cache.json"


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_programs(db_dir):
    """Load all program JSON files from the database directory."""
    programs = []
    pattern = os.path.join(db_dir, "programs", "*.json")
    for filepath in sorted(glob.glob(pattern)):
        with open(filepath, "r") as f:
            programs.append(json.load(f))
    return programs


def load_log(log_dir):
    """Return the path of the most recent log file (for the summary table)."""
    log_files = sorted(glob.glob(os.path.join(log_dir, "openevolve_*.log")))
    return log_files[-1] if log_files else None


def order_by_iteration(programs):
    """Attach a real iteration index, preferring the authoritative field.

    OpenEvolve records `iteration_found` on each program; fall back to
    timestamp rank only if it is missing.
    """
    has_iter = all("iteration_found" in p for p in programs)
    if has_iter:
        for p in programs:
            p["_iteration"] = p.get("iteration_found", 0)
    else:
        for i, p in enumerate(sorted(programs, key=lambda p: p.get("timestamp", 0))):
            p["_iteration"] = i
    return programs


# --------------------------------------------------------------------------- #
# Baselines (LRU / LFU human-written, Belady-MIN clairvoyant ceiling)
# --------------------------------------------------------------------------- #
def compute_baselines(cache_bytes=CACHE_SIZE_BYTES, n=MAX_RECORDS):
    """Compute LRU, LFU and Belady/MIN hit rates on the same trace/slice.

    Cached to BASELINE_CACHE keyed by (cache_bytes, n) so repeat runs are
    instant. Returns a dict of hit rates, or None if dependencies/trace are
    unavailable (charts then degrade gracefully).
    """
    key = f"{cache_bytes}_{n}"
    if os.path.exists(BASELINE_CACHE):
        try:
            cached = json.load(open(BASELINE_CACHE))
            if key in cached:
                return cached[key]
        except Exception:
            pass

    try:
        import cache_simulator as cs
        import compute_optimal as co
    except Exception as e:
        print(f"[baselines] skipped (import failed: {e})")
        return None

    if not os.path.exists(TRACE_FILE):
        print(f"[baselines] skipped (trace not found: {TRACE_FILE})")
        return None

    records = cs.parse_trace(TRACE_FILE)[:n]
    simple = [(oid, sz) for _ts, oid, sz, _na in records]

    def run(cache):
        for oid, sz in simple:
            cache.access(oid, sz)
        return cache.hit_rate()

    lru = run(cs.LRUCache(cache_bytes))
    lfu = run(cs.LFUCache(cache_bytes))
    co.CAPACITY = cache_bytes
    mn = co.belady_hit_rate(simple)

    result = {"LRU": lru, "LFU": lfu, "MIN": mn}

    try:
        existing = json.load(open(BASELINE_CACHE)) if os.path.exists(BASELINE_CACHE) else {}
        existing[key] = result
        with open(BASELINE_CACHE, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass

    return result


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def running_max(values):
    """Cumulative maximum (the evolution frontier / staircase)."""
    best = float("-inf")
    out = []
    for v in values:
        best = max(best, v)
        out.append(best)
    return out


def status_color(time_s, time_factor, error):
    """Color a program by its scoring status."""
    if error == "timeout":
        return "#d62728"          # red
    if error:
        return "#ff7f0e"          # orange
    if time_factor and time_factor >= 1.05:
        return "#2ca02c"          # green - earned the speed bonus
    if time_factor and time_factor < 1.0:
        return "#9467bd"          # purple - penalized (slow or below baseline)
    return "#1f77b4"              # blue - neutral (1.0)


# --------------------------------------------------------------------------- #
# Glossary / background block
# --------------------------------------------------------------------------- #
def glossary_html():
    return """
    <details open style="font-family: sans-serif; padding: 16px 24px; background:#eef3f8;
        border-bottom:2px solid #c8d6e5;">
      <summary style="font-size:1.2em; font-weight:bold; cursor:pointer;">
        Background &amp; Glossary &mdash; what is being measured</summary>
      <div style="margin-top:12px; line-height:1.5; max-width:1100px;">
        <p><b>The task.</b> Evolve a <i>cache replacement policy</i>. A cache holds a
        limited number of bytes (here <b>16&nbsp;MB</b>). Each request asks for an object;
        if it is already cached that is a <b>hit</b>, otherwise a <b>miss</b> and the object
        is inserted. When there is no room, the policy must choose which object(s) to
        <b>evict</b>. We replay a real storage trace (<code>msr_hm_0</code>,
        50,000 requests) and measure how often the policy scores hits.</p>

        <p><b>Hit rate</b> = hits / total accesses. This is the primary quality metric
        (higher is better). Everything else is in service of raising it.</p>

        <p><b>Human-written baselines</b> (the bar we must clear):</p>
        <ul>
          <li><b>LRU</b> (Least Recently Used) &mdash; evict the object unused for the
          longest time. Simple and strong; this is the seed the evolution starts from.</li>
          <li><b>LFU</b> (Least Frequently Used) &mdash; evict the object with the fewest
          accesses so far.</li>
        </ul>

        <p><b>GDSF</b> (Greedy-Dual-Size-Frequency) &mdash; the strategy the evolution
        discovered: rank objects by <i>frequency / size</i> plus an aging "clock", and add
        an <b>admission filter</b> that refuses brand-new objects when the cache is full
        (so one-hit-wonders don't evict useful data).</p>

        <p><b>Belady / MIN</b> &mdash; a <i>clairvoyant</i> reference that looks at future
        requests and evicts whatever is needed farthest ahead. It cannot be built in
        practice (it cheats by knowing the future), so it serves as the <b>ceiling</b> that
        bounds how good any real policy could be. <b>Headroom</b> = MIN &minus; LRU;
        <b>headroom captured</b> = (Evolved &minus; LRU) / (MIN &minus; LRU).</p>

        <p><b>Combined score</b> &mdash; the optimizer's reward, not the hit rate itself:
        <code>score = hit_rate &times; time_factor</code>. The <code>time_factor</code> is
        <b>1.05</b> when evaluation runs under 2&nbsp;s (a speed bonus), 1.0 up to 25&nbsp;s,
        a graduated penalty beyond that, and is halved (&times;0.5) if the hit rate falls
        below the LRU baseline. <i>Note:</i> the LRU seed sits a hair below the configured
        baseline threshold, so its own score is artificially halved &mdash; a calibration
        artifact, flagged in the charts, that does not affect any evolved program.</p>

        <p><b>OpenEvolve terms:</b></p>
        <ul>
          <li><b>Iteration</b> &mdash; one LLM-generated candidate program, then evaluated.</li>
          <li><b>Generation</b> &mdash; evolutionary depth: how many mutation steps from the seed.</li>
          <li><b>Island</b> &mdash; an independently evolving sub-population; good ideas
          migrate between islands periodically, which preserves diversity.</li>
          <li><b>MAP-Elites</b> &mdash; keeps the best program in each region of a feature
          grid rather than a single global winner, so varied strategies survive.</li>
          <li><b>Evolution tree</b> &mdash; parent &rarr; child links showing which program
          was mutated into which.</li>
        </ul>
      </div>
    </details>
    """


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def generate_html_report(programs, log_path, output_path, baselines):
    programs = order_by_iteration(programs)

    initial = [p for p in programs if p.get("parent_id") is None]
    evolved = sorted(programs, key=lambda p: p.get("_iteration", 0))

    def m(p, key, default=0):
        return p.get("metrics", {}).get(key, default)

    def art(p, key, default=None):
        return p.get("metrics", {}).get("artifacts", {}).get(key, default)

    iterations = [p["_iteration"] for p in evolved]
    scores = [m(p, "combined_score") for p in evolved]
    hit_rates = [m(p, "hit_rate") for p in evolved]
    times = [m(p, "time") for p in evolved]
    tfactors = [art(p, "time_factor") for p in evolved]
    errors = [art(p, "error") for p in evolved]
    colors = [status_color(t, tf, e) for t, tf, e in zip(times, tfactors, errors)]

    score_frontier = running_max(scores)
    hr_frontier = running_max(hit_rates)

    best_prog = max(programs, key=lambda p: m(p, "combined_score"))
    best_score = m(best_prog, "combined_score")
    best_hr = m(best_prog, "hit_rate")

    # Seed (initial LRU) coordinates for the artifact annotation.
    seed = initial[0] if initial else None
    seed_iter = seed["_iteration"] if seed else None
    seed_score = m(seed, "combined_score") if seed else None

    b = baselines or {}
    lru, lfu, mn = b.get("LRU"), b.get("LFU"), b.get("MIN")

    if lru is not None and mn is not None and mn > lru:
        captured = (best_hr - lru) / (mn - lru) * 100
        bar_title = f"Hit Rate: Evolved vs Baselines — captures {captured:.0f}% of LRU→MIN headroom"
    else:
        bar_title = "Hit Rate: Evolved vs Baselines vs Optimal Ceiling"

    fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=(
            bar_title,
            "Score Progress (best-so-far frontier)",
            "Hit Rate per Iteration (with baseline references)",
            "Execution Time per Iteration (scoring thresholds)",
            "Evolution Tree (generation vs score)",
            "Best Score per Island Over Iterations",
        ),
        specs=[
            [{"type": "bar"}, {"type": "scatter"}],
            [{"type": "scatter"}, {"type": "scatter"}],
            [{"type": "scatter"}, {"type": "scatter"}],
        ],
        vertical_spacing=0.11,
        horizontal_spacing=0.14,
    )

    # ---- (1,1) Headline: baseline comparison bar -------------------------- #
    bar_names, bar_vals, bar_colors = [], [], []
    if lru is not None:
        bar_names.append("LRU<br>(seed)"); bar_vals.append(lru); bar_colors.append("#8c8c8c")
    if lfu is not None:
        bar_names.append("LFU"); bar_vals.append(lfu); bar_colors.append("#8c8c8c")
    bar_names.append("Evolved<br>(GDSF)"); bar_vals.append(best_hr); bar_colors.append("#2ca02c")
    if mn is not None:
        bar_names.append("MIN<br>(ceiling)"); bar_vals.append(mn); bar_colors.append("#d62728")

    fig.add_trace(
        go.Bar(x=bar_names, y=bar_vals, marker_color=bar_colors,
               text=[f"{v:.4f}" for v in bar_vals], textposition="outside",
               showlegend=False, hoverinfo="y"),
        row=1, col=1,
    )
    if lru is not None and mn is not None and mn > lru:
        fig.add_hline(y=lru, line_dash="dot", line_color="#8c8c8c", row=1, col=1)
        fig.add_hline(y=mn, line_dash="dot", line_color="#d62728", row=1, col=1)
    # Headroom so the "outside" bar labels are not clipped at the top.
    fig.update_yaxes(title_text="Hit Rate", range=[0, max(bar_vals) * 1.18],
                     row=1, col=1)

    # ---- (1,2) Score frontier --------------------------------------------- #
    fig.add_trace(
        go.Scatter(x=iterations, y=scores, mode="markers",
                   marker=dict(color=colors, size=7, line=dict(width=0.5, color="black")),
                   name="candidate score",
                   text=[f"iter {i}: {s:.4f}" for i, s in zip(iterations, scores)],
                   hoverinfo="text"),
        row=1, col=2,
    )
    fig.add_trace(
        go.Scatter(x=iterations, y=score_frontier, mode="lines",
                   line=dict(color="#1f77b4", width=2.5), name="best so far"),
        row=1, col=2,
    )
    if seed is not None:
        fig.add_trace(
            go.Scatter(x=[seed_iter], y=[seed_score], mode="markers",
                       marker=dict(symbol="x", size=13, color="#d62728",
                                   line=dict(width=2)),
                       name="LRU seed (penalized artifact)",
                       hovertext=["LRU seed: score halved by baseline calibration boundary"],
                       hoverinfo="text"),
            row=1, col=2,
        )
    fig.update_xaxes(title_text="Iteration", row=1, col=2)
    fig.update_yaxes(title_text="Combined Score", row=1, col=2)

    # ---- (2,1) Hit rate per iteration + baseline references --------------- #
    fig.add_trace(
        go.Scatter(x=iterations, y=hit_rates, mode="markers",
                   marker=dict(color=colors, size=7, line=dict(width=0.5, color="black")),
                   name="candidate hit rate",
                   text=[f"iter {i}: {h:.4f}" for i, h in zip(iterations, hit_rates)],
                   hoverinfo="text", showlegend=False),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(x=iterations, y=hr_frontier, mode="lines",
                   line=dict(color="#2ca02c", width=2.5),
                   name="best hit rate so far", showlegend=False),
        row=2, col=1,
    )
    for val, color, label in ((lru, "#8c8c8c", "LRU"),
                              (lfu, "#1f77b4", "LFU"),
                              (mn, "#d62728", "MIN")):
        if val is not None:
            fig.add_hline(y=val, line_dash="dash", line_color=color,
                          annotation_text=f"{label} {val:.4f}",
                          annotation_position="top left",
                          annotation_font_size=10, row=2, col=1)
    fig.update_xaxes(title_text="Iteration", row=2, col=1)
    fig.update_yaxes(title_text="Hit Rate", row=2, col=1)

    # ---- (2,2) Execution time + scoring thresholds ------------------------ #
    fig.add_trace(
        go.Scatter(x=iterations, y=times, mode="markers",
                   marker=dict(color=colors, size=8, line=dict(width=0.5, color="black")),
                   name="eval time",
                   text=[f"iter {i}: {t:.2f}s (tf={tf})" for i, t, tf in
                         zip(iterations, times, tfactors)],
                   hoverinfo="text", showlegend=False),
        row=2, col=2,
    )
    fig.add_hline(y=FAST_THRESHOLD, line_dash="dash", line_color="#2ca02c",
                  annotation_text="2s — speed bonus below",
                  annotation_position="bottom right", annotation_font_size=10,
                  row=2, col=2)
    fig.add_hline(y=SLOW_THRESHOLD, line_dash="dot", line_color="#ff7f0e",
                  annotation_text="25s — penalty above",
                  annotation_position="top left", annotation_font_size=10,
                  row=2, col=2)
    fig.add_hline(y=TIMEOUT, line_dash="dash", line_color="#d62728",
                  annotation_text="30s — timeout",
                  annotation_position="top right", annotation_font_size=10,
                  row=2, col=2)
    fig.update_xaxes(title_text="Iteration", row=2, col=2)
    fig.update_yaxes(title_text="Time (seconds)", row=2, col=2)

    # ---- (3,1) Evolution tree (generation vs score) ----------------------- #
    edge_x, edge_y = [], []
    prog_by_id = {p["id"]: p for p in programs}
    for p in programs:
        pid = p.get("parent_id")
        parent = prog_by_id.get(pid)
        if parent:
            edge_x += [parent.get("generation", 0), p.get("generation", 0), None]
            edge_y += [m(parent, "combined_score"), m(p, "combined_score"), None]
    fig.add_trace(
        go.Scatter(x=edge_x, y=edge_y, mode="lines",
                   line=dict(color="lightgray", width=1),
                   showlegend=False, hoverinfo="skip"),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=[p.get("generation", 0) for p in programs],
            y=[m(p, "combined_score") for p in programs],
            mode="markers",
            marker=dict(color=[p.get("metadata", {}).get("island", 0) for p in programs],
                        colorscale="Viridis", size=9, showscale=False),
            text=[f"id {p['id'][:8]}  gen {p.get('generation', 0)}  "
                  f"score {m(p, 'combined_score'):.4f}" for p in programs],
            hoverinfo="text", showlegend=False),
        row=3, col=1,
    )
    fig.update_xaxes(title_text="Generation", row=3, col=1)
    fig.update_yaxes(title_text="Combined Score", row=3, col=1)

    # ---- (3,2) Best score per island over iterations ---------------------- #
    islands = {}
    for p in programs:
        isl = p.get("metadata", {}).get("island", -1)
        islands.setdefault(isl, []).append((p["_iteration"], m(p, "combined_score")))
    palette = ["#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#d62728"]
    for idx, (isl, pts) in enumerate(sorted(islands.items())):
        pts.sort(key=lambda x: x[0])
        xs = [x for x, _ in pts]
        ys = running_max([y for _, y in pts])
        fig.add_trace(
            go.Scatter(x=xs, y=ys, mode="lines+markers",
                       line=dict(color=palette[idx % len(palette)], width=2),
                       marker=dict(size=5), name=f"Island {isl}"),
            row=3, col=2,
        )
    fig.update_xaxes(title_text="Iteration", row=3, col=2)
    fig.update_yaxes(title_text="Best Score So Far", row=3, col=2)

    fig.update_layout(
        title="OpenEvolve Cache-Policy Evolution Report — 16MB, msr_hm_0 (50k)",
        title_x=0.5,
        height=1400, autosize=True, template="plotly_white",
        margin=dict(l=80, r=80, t=120, b=110),
        legend=dict(orientation="h", yanchor="top", y=-0.05,
                    xanchor="center", x=0.5),
    )

    # ---- Summary KPIs ----------------------------------------------------- #
    timeout_count = sum(1 for p in programs if art(p, "error") == "timeout")
    error_count = sum(1 for p in programs
                      if art(p, "error") and art(p, "error") != "timeout")

    def pp(x):
        return f"{x * 100:+.2f} pp"

    rows = [
        ("Programs evaluated", str(len(programs))),
        ("Best hit rate (evolved)", f"{best_hr:.4f}"),
        ("Best combined score", f"{best_score:.4f}"),
    ]
    if lru is not None:
        rows.append(("LRU baseline (hit rate)", f"{lru:.4f}"))
        rows.append(("Δ vs LRU", pp(best_hr - lru)))
    if lfu is not None:
        rows.append(("LFU baseline (hit rate)", f"{lfu:.4f}"))
        rows.append(("Δ vs LFU", pp(best_hr - lfu)))
    if mn is not None:
        rows.append(("Belady/MIN ceiling (hit rate)", f"{mn:.4f}"))
        if lru is not None and mn > lru:
            rows.append(("Headroom captured (LRU→MIN)",
                         f"{(best_hr - lru) / (mn - lru) * 100:.1f}%"))
    rows += [
        ("Timeouts", str(timeout_count)),
        ("Errors", str(error_count)),
        ("Log file", str(log_path or "N/A")),
    ]

    row_html = "".join(
        f'<tr><td style="padding:6px 14px;"><strong>{k}:</strong></td>'
        f'<td style="padding:6px 14px;">{v}</td></tr>' for k, v in rows
    )
    summary_html = f"""
    <div style="font-family: sans-serif; padding: 20px 24px; background:#f8f9fa;
        border-bottom:2px solid #dee2e6;">
        <h2 style="margin-top:0;">Evolution Summary</h2>
        <table style="border-collapse: collapse;">{row_html}</table>
    </div>
    """

    page_title = (
        '<h1 style="font-family:sans-serif; text-align:center; '
        'padding:28px 16px 4px; margin:0; color:#1a2a3a;">'
        'Evolving Cache Replacement Policies with OpenEvolve</h1>'
    )

    fig_html = fig.to_html(include_plotlyjs=True, default_width="100%",
                           default_height="1400px")
    fig_html = fig_html.replace(
        "<body>", f"<body>{page_title}{glossary_html()}{summary_html}")

    with open(output_path, "w") as f:
        f.write(fig_html)
    print(f"Report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Visualize OpenEvolve cache results")
    parser.add_argument("--db-dir", default="./openevolve_db", help="Database directory")
    parser.add_argument("--log-dir", default="./openevolve_output/logs", help="Log directory")
    parser.add_argument("--output", default="evolution_report.html", help="Output HTML file")
    args = parser.parse_args()

    programs = load_programs(args.db_dir)
    if not programs:
        print("No programs found in database directory.")
        return

    log_path = load_log(args.log_dir)
    baselines = compute_baselines()
    if baselines:
        print(f"[baselines] LRU={baselines['LRU']:.4f}  "
              f"LFU={baselines['LFU']:.4f}  MIN={baselines['MIN']:.4f}")
    generate_html_report(programs, log_path, args.output, baselines)


if __name__ == "__main__":
    main()
