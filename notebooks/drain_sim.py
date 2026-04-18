# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo",
#     "matplotlib",
#     "numpy",
# ]
# ///

import marimo

__generated_with = "0.23.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import numpy as np
    import matplotlib.pyplot as plt

    return mo, np, plt


@app.cell
def _(mo):
    mo.md("""
    # Drain Buffer Simulation 🦆

    Simulating Anthropic's streaming pattern: **~57 chars every ~487ms**
    (measured from real Logfire data, Opus 4.6 with 1M context).

    The goal: find a drain equation that produces smooth, steady text
    output from bursty input. The buffer absorbs the bursts; the drain
    rate converts them to smooth character-by-character display.
    """)
    return


@app.cell
def _(mo):
    # Input parameters — Anthropic's measured streaming profile
    chunk_size = mo.ui.slider(10, 100, value=57, label="Chunk size (chars)")
    chunk_interval = mo.ui.slider(100, 1000, value=487, step=10, label="Chunk interval (ms)")
    num_chunks = mo.ui.slider(5, 60, value=25, label="Number of chunks")

    mo.md(f"""
    ## Anthropic Streaming Profile

    {chunk_size}
    {chunk_interval}
    {num_chunks}
    """)
    return chunk_interval, chunk_size, num_chunks


@app.cell
def _(mo):
    # Drain algorithm parameters
    chase_factor = mo.ui.slider(0.1, 5.0, value=1.0, step=0.1, label="Chase factor")
    base_rate = mo.ui.slider(0, 200, value=0, step=5, label="Base rate (chars/sec)")
    ema_alpha = mo.ui.slider(0.001, 0.2, value=0.05, step=0.001, label="EMA alpha (smoothing)")
    frame_rate = mo.ui.slider(30, 144, value=60, step=1, label="Frame rate (fps)")

    mo.md(f"""
    ## Drain Algorithm Parameters

    {chase_factor}
    {base_rate}
    {ema_alpha}
    {frame_rate}

    **Current equation:** `rate = base_rate + remaining * chase_factor`
    (with EMA smoothing on the rate)
    """)
    return base_rate, chase_factor, ema_alpha, frame_rate


@app.cell(hide_code=True)
def _(
    base_rate,
    chase_factor,
    chunk_interval,
    chunk_size,
    ema_alpha,
    frame_rate,
    np,
    num_chunks,
):
    # Simulation
    fps = frame_rate.value
    dt = 1.0 / fps  # seconds per frame
    total_time = (num_chunks.value * chunk_interval.value / 1000) + 2.0  # extra 2s for drain
    num_frames = int(total_time * fps)

    # Pre-compute chunk arrival times
    chunk_times = [i * chunk_interval.value / 1000.0 for i in range(num_chunks.value)]

    # State
    buffer = 0.0
    smoothed_rate = float(base_rate.value)
    displayed = 0
    total_text = 0
    char_remainder = 0.0

    # Recording arrays
    times = np.zeros(num_frames)
    buffer_depths = np.zeros(num_frames)
    drain_rates = np.zeros(num_frames)
    displayed_counts = np.zeros(num_frames)
    chars_per_frame = np.zeros(num_frames)

    next_chunk_idx = 0

    for frame in range(num_frames):
        t = frame * dt
        times[frame] = t

        # Add chunks that arrive at this time
        while next_chunk_idx < len(chunk_times) and chunk_times[next_chunk_idx] <= t:
            buffer += chunk_size.value
            total_text += chunk_size.value
            next_chunk_idx += 1

        # Compute drain
        remaining = buffer
        target_rate = base_rate.value + remaining * chase_factor.value
        # EMA smoothing (frame-rate independent)
        corrected_alpha = 1 - (1 - ema_alpha.value) ** (dt * 60)
        smoothed_rate += (target_rate - smoothed_rate) * corrected_alpha

        # Accumulate and drain
        char_remainder += smoothed_rate * dt
        budget = int(char_remainder)
        if budget > 0:
            char_remainder -= budget
            actual_drain = min(budget, int(buffer))
            buffer -= actual_drain
            displayed += actual_drain
            chars_per_frame[frame] = actual_drain
        else:
            chars_per_frame[frame] = 0

        buffer_depths[frame] = buffer
        drain_rates[frame] = smoothed_rate
        displayed_counts[frame] = displayed

    # Effective chars/sec (windowed over last 0.5 seconds)
    window = int(0.5 * fps)
    effective_cps = np.zeros(num_frames)
    for i in range(window, num_frames):
        effective_cps[i] = np.sum(chars_per_frame[i-window:i]) / (window * dt)
    return (
        buffer_depths,
        displayed_counts,
        drain_rates,
        effective_cps,
        times,
        total_text,
    )


@app.cell(hide_code=True)
def _(
    base_rate,
    buffer_depths,
    chase_factor,
    chunk_interval,
    chunk_size,
    displayed_counts,
    drain_rates,
    effective_cps,
    np,
    plt,
    times,
    total_text,
):
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    # Buffer depth
    ax1 = axes[0]
    ax1.fill_between(times, buffer_depths, alpha=0.3, color='#f0c040')
    ax1.plot(times, buffer_depths, color='#f0c040', linewidth=1.5)
    ax1.set_ylabel('Buffer depth (chars)')
    ax1.set_title(f'Buffer Simulation — {chunk_size.value} chars every {chunk_interval.value}ms, '
                   f'chase={chase_factor.value}, base={base_rate.value}')
    ax1.axhline(y=0, color='gray', linewidth=0.5)
    ax1.grid(True, alpha=0.2)

    # Drain rate
    ax2 = axes[1]
    ax2.plot(times, drain_rates, color='#40c0f0', linewidth=1.5, label='Smoothed rate')
    ax2.plot(times, effective_cps, color='#f04040', linewidth=1, alpha=0.7, label='Effective c/s (0.5s window)')
    input_rate = chunk_size.value / (chunk_interval.value / 1000)
    ax2.axhline(y=input_rate, color='green', linewidth=1, linestyle='--', alpha=0.5, label=f'Input rate ({input_rate:.0f} c/s)')
    ax2.set_ylabel('Chars/sec')
    ax2.legend(loc='upper right')
    ax2.grid(True, alpha=0.2)

    # Displayed text
    ax3 = axes[2]
    total_available = np.minimum(
        np.cumsum([chunk_size.value if i * (chunk_interval.value/1000) <= t else 0
                   for i, t in enumerate(np.repeat(times, 1))]),
        total_text
    )
    ax3.plot(times, displayed_counts, color='#40f040', linewidth=1.5, label='Displayed')
    ax3.set_ylabel('Chars displayed')
    ax3.set_xlabel('Time (seconds)')
    ax3.grid(True, alpha=0.2)

    plt.tight_layout()
    fig
    return


@app.cell
def _(
    buffer_depths,
    chunk_interval,
    chunk_size,
    drain_rates,
    effective_cps,
    mo,
    np,
):
    _input_rate = chunk_size.value / (chunk_interval.value / 1000)
    streaming_end = len(drain_rates) * 0.7  # approximate

    mo.md(f"""
    ## Summary

    | Metric | Value |
    |---|---|
    | Input rate | {_input_rate:.0f} chars/sec |
    | Peak buffer | {np.max(buffer_depths):.0f} chars |
    | Avg buffer (during streaming) | {np.mean(buffer_depths[:int(streaming_end)]):.0f} chars |
    | Avg effective c/s (during streaming) | {np.mean(effective_cps[30:int(streaming_end)]):.0f} chars/sec |
    | Buffer empty at end? | {'Yes ✓' if buffer_depths[-1] < 1 else f'No — {buffer_depths[-1]:.0f} chars remaining'} |
    """)
    return


if __name__ == "__main__":
    app.run()
