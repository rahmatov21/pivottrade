"""
Pivot Points High Low & Missed Reversal Levels [LuxAlgo]  --  Python port
===========================================================================

A bar-for-bar re-implementation of the Pine Script v5 indicator
"Pivot Points High Low & Missed Reversal Levels [LuxAlgo]".

It reproduces the exact state machine the original script runs, so the
same `high`/`low` series and the same `length` will produce the same:

  * Regular pivots        -> confirmed `length`-bar pivot highs/lows
                              (the ta.pivothigh / ta.pivotlow calls).
  * Missed ("ghost") pivots -> reversal points price swung through but
                              never confirmed as an actual pivot
                              (the little ghost markers in the script).
  * The zig-zag line connecting every regular + missed point, in the
                              order they occurred, with solid/dashed styling logic.
  * "Ghost level" stubs   -> the short horizontal marker the original
                              script draws at every missed pivot.
  * The live/last-bar projection -> the swing that is currently
                              building on the most recent bars (barstate.islast).

Repainting note
----------------
Exactly like the original indicator, a "regular pivot" at bar index
`p` is only known `length` bars later, at bar `p + length`. Each
RegularPivot / MissedPivot carries both its own `bar` / `time` (where it
sits on the chart) and `confirmed_at` / `confirmed_time` (the first bar index
at which the script actually detected and plotted it).

For automated trading, trading signals must trigger at `confirmed_at` /
`confirmed_time` (never at `bar`) to guarantee zero repainting / lookahead.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Sequence, Literal, Dict, Any

PivotType = Literal["high", "low"]


@dataclass
class RegularPivot:
    bar: int                    # bar index of the pivot itself (== n - length)
    price: float
    type: PivotType
    confirmed_at: int           # bar index at which this pivot became known/plotted
    time: Optional[int] = None  # unix timestamp in seconds
    confirmed_time: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MissedPivot:
    bar: int                    # bar index of the missed reversal point
    price: float
    type: PivotType
    confirmed_at: int           # bar index at which the "ghost" marker was drawn
    time: Optional[int] = None  # unix timestamp in seconds
    confirmed_time: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Segment:
    """One drawn line segment (zig-zag leg or ghost-level stub)."""
    x1: int
    y1: float
    x2: int
    y2: float
    style: str = "solid"        # "solid" | "dashed"
    kind: str = "zigzag"        # "zigzag" | "ghost_level" | "projection"
    t1: Optional[int] = None    # unix timestamp of start point
    t2: Optional[int] = None    # unix timestamp of end point

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PivotResult:
    regular_pivots: List[RegularPivot] = field(default_factory=list)
    missed_pivots: List[MissedPivot] = field(default_factory=list)
    zigzag: List[Segment] = field(default_factory=list)
    ghost_levels: List[Segment] = field(default_factory=list)

    # The un-confirmed swing currently forming on the very last bar
    # (mirrors the `barstate.islast` block).
    projection_point: Optional[MissedPivot] = None
    projection_zigzag: Optional[Segment] = None
    projection_ghost_level: Optional[Segment] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regular_pivots": [p.to_dict() for p in self.regular_pivots],
            "missed_pivots": [m.to_dict() for m in self.missed_pivots],
            "zigzag": [z.to_dict() for z in self.zigzag],
            "ghost_levels": [g.to_dict() for g in self.ghost_levels],
            "projection_point": self.projection_point.to_dict() if self.projection_point else None,
            "projection_zigzag": self.projection_zigzag.to_dict() if self.projection_zigzag else None,
            "projection_ghost_level": self.projection_ghost_level.to_dict() if self.projection_ghost_level else None,
        }


# --------------------------------------------------------------------------- #
# ta.pivothigh(leftbars, rightbars) / ta.pivotlow(leftbars, rightbars)
# --------------------------------------------------------------------------- #
def _pivot_high(high: Sequence[float], p: int, leftbars: int, rightbars: int) -> Optional[float]:
    if p - leftbars < 0 or p + rightbars >= len(high):
        return None
    val = high[p]
    for i in range(p - leftbars, p + rightbars + 1):
        if i != p and high[i] >= val:
            return None
    return val


def _pivot_low(low: Sequence[float], p: int, leftbars: int, rightbars: int) -> Optional[float]:
    if p - leftbars < 0 or p + rightbars >= len(low):
        return None
    val = low[p]
    for i in range(p - leftbars, p + rightbars + 1):
        if i != p and low[i] <= val:
            return None
    return val


def pivot_points_high_low_missed(
    high: Sequence[float],
    low: Sequence[float],
    length: int = 50,
    show_reg: bool = True,
    show_miss: bool = True,
    timestamps: Optional[Sequence[int]] = None,
) -> PivotResult:
    """
    Parameters
    ----------
    high, low  : sequences of float, same length, oldest -> newest
                 (bar index 0 == first/oldest bar, same convention as Pine).
    length     : 'Pivot Length' input (left and right strength both equal length).
    show_reg   : emit regular (confirmed) pivots.
    show_miss  : emit missed / ghost pivots and levels.
    timestamps : optional sequence of timestamps (unix epoch seconds).

    Returns
    -------
    PivotResult
    """
    n_bars = len(high)
    if len(low) != n_bars:
        raise ValueError("high and low must be the same length")
    if timestamps is not None and len(timestamps) != n_bars:
        raise ValueError("timestamps must be the same length as high/low")
    if n_bars == 0:
        return PivotResult()

    result = PivotResult()

    def get_time(idx: int) -> Optional[int]:
        if timestamps is not None and 0 <= idx < len(timestamps):
            return int(timestamps[idx])
        return None

    # ---- persistent state, mirrors every `var` declaration in the script --
    # Initialize to first bar's price to avoid 0.0 tracking corruption
    max_ = float(high[0])
    min_ = float(low[0])
    max_x1 = 0
    min_x1 = 0
    follow_max = float(high[0])
    follow_max_x1 = 0
    follow_min = float(low[0])
    follow_min_x1 = 0
    os = 0
    py1: Optional[float] = None
    px1: Optional[int] = None

    ghost_level: Optional[Segment] = None  # currently "open" ghost-level stub

    def add_zigzag_segment(x1: Optional[int], y1: Optional[float], x2: int, y2: float, style: str = "solid") -> None:
        if x1 is not None and y1 is not None and (x1 != x2 or y1 != y2):
            result.zigzag.append(
                Segment(x1=x1, y1=y1, x2=x2, y2=y2, style=style, kind="zigzag",
                        t1=get_time(x1), t2=get_time(x2))
            )

    for n in range(n_bars):
        # values of state committed at end of previous bar (== Pine's [1])
        prev_max, prev_min = max_, min_
        prev_follow_max, prev_follow_min = follow_max, follow_min
        prev_os = os

        # ---- pivot detection (confirmation lags `length` bars) -----------
        ph: Optional[float] = None
        pl: Optional[float] = None
        if n >= 2 * length:
            ph = _pivot_high(high, n - length, length, length)
            pl = _pivot_low(low, n - length, length, length)

        # ---- running max / min / follow trackers --------------------------
        if n >= length:
            h_len = high[n - length]
            l_len = low[n - length]
            max_ = max(h_len, max_)
            min_ = min(l_len, min_)
            follow_max = max(h_len, follow_max)
            follow_min = min(l_len, follow_min)

            if max_ > prev_max:
                max_x1 = n - length
                follow_min = l_len
            if min_ < prev_min:
                min_x1 = n - length
                follow_max = h_len

            if follow_min < prev_follow_min:
                follow_min_x1 = n - length
            if follow_max > prev_follow_max:
                follow_max_x1 = n - length

        # ---- extend the currently-open ghost-level stub up to "now" ------
        if ghost_level is not None:
            ghost_level.x2 = n
            ghost_level.t2 = get_time(n)

        # ============================ pivot HIGH ==========================
        if ph is not None:
            if show_miss:
                if prev_os == 1:
                    result.missed_pivots.append(
                        MissedPivot(
                            bar=min_x1, price=min_, type="low", confirmed_at=n,
                            time=get_time(min_x1), confirmed_time=get_time(n)
                        )
                    )

                    add_zigzag_segment(px1, py1, min_x1, min_, "dashed")
                    px1, py1 = min_x1, min_

                    if ghost_level is not None:
                        ghost_level.x2 = px1
                        ghost_level.t2 = get_time(px1)
                    ghost_level = Segment(
                        px1, py1, px1, py1, kind="ghost_level",
                        t1=get_time(px1), t2=get_time(px1)
                    )
                    result.ghost_levels.append(ghost_level)

                elif ph < max_:
                    result.missed_pivots.append(
                        MissedPivot(
                            bar=max_x1, price=max_, type="high", confirmed_at=n,
                            time=get_time(max_x1), confirmed_time=get_time(n)
                        )
                    )
                    result.missed_pivots.append(
                        MissedPivot(
                            bar=follow_min_x1, price=follow_min, type="low", confirmed_at=n,
                            time=get_time(follow_min_x1), confirmed_time=get_time(n)
                        )
                    )

                    add_zigzag_segment(px1, py1, max_x1, max_, "dashed")
                    px1, py1 = max_x1, max_

                    if ghost_level is not None:
                        ghost_level.x2 = px1
                        ghost_level.t2 = get_time(px1)
                    ghost_level = Segment(
                        px1, py1, px1, py1, kind="ghost_level",
                        t1=get_time(px1), t2=get_time(px1)
                    )
                    result.ghost_levels.append(ghost_level)

                    add_zigzag_segment(px1, py1, follow_min_x1, follow_min, "dashed")
                    px1, py1 = follow_min_x1, follow_min

                    ghost_level.x2 = px1  # extends the stub just created above
                    ghost_level.t2 = get_time(px1)
                    ghost_level = Segment(
                        px1, py1, px1, py1, kind="ghost_level",
                        t1=get_time(px1), t2=get_time(px1)
                    )
                    result.ghost_levels.append(ghost_level)

            if show_reg:
                pivot_bar = n - length
                result.regular_pivots.append(
                    RegularPivot(
                        bar=pivot_bar, price=ph, type="high", confirmed_at=n,
                        time=get_time(pivot_bar), confirmed_time=get_time(n)
                    )
                )
                style = "dashed" if (ph < max_ or prev_os == 1) else "solid"
                add_zigzag_segment(px1, py1, pivot_bar, ph, style)

            py1, px1 = ph, n - length
            os = 1
            max_, min_ = ph, ph

        # ============================ pivot LOW ============================
        if pl is not None:
            if show_miss:
                if prev_os == 0:
                    result.missed_pivots.append(
                        MissedPivot(
                            bar=max_x1, price=max_, type="high", confirmed_at=n,
                            time=get_time(max_x1), confirmed_time=get_time(n)
                        )
                    )

                    add_zigzag_segment(px1, py1, max_x1, max_, "dashed")
                    px1, py1 = max_x1, max_

                    if ghost_level is not None:
                        ghost_level.x2 = px1
                        ghost_level.t2 = get_time(px1)
                    ghost_level = Segment(
                        px1, py1, px1, py1, kind="ghost_level",
                        t1=get_time(px1), t2=get_time(px1)
                    )
                    result.ghost_levels.append(ghost_level)

                elif pl > min_:
                    result.missed_pivots.append(
                        MissedPivot(
                            bar=follow_max_x1, price=follow_max, type="high", confirmed_at=n,
                            time=get_time(follow_max_x1), confirmed_time=get_time(n)
                        )
                    )
                    result.missed_pivots.append(
                        MissedPivot(
                            bar=min_x1, price=min_, type="low", confirmed_at=n,
                            time=get_time(min_x1), confirmed_time=get_time(n)
                        )
                    )

                    add_zigzag_segment(px1, py1, min_x1, min_, "dashed")
                    px1, py1 = min_x1, min_

                    if ghost_level is not None:
                        ghost_level.x2 = px1
                        ghost_level.t2 = get_time(px1)
                    ghost_level = Segment(
                        px1, py1, px1, py1, kind="ghost_level",
                        t1=get_time(px1), t2=get_time(px1)
                    )
                    result.ghost_levels.append(ghost_level)

                    add_zigzag_segment(px1, py1, follow_max_x1, follow_max, "dashed")
                    px1, py1 = follow_max_x1, follow_max

                    ghost_level.x2 = px1
                    ghost_level.t2 = get_time(px1)
                    ghost_level = Segment(
                        px1, py1, px1, py1, kind="ghost_level",
                        t1=get_time(px1), t2=get_time(px1)
                    )
                    result.ghost_levels.append(ghost_level)

            if show_reg:
                pivot_bar = n - length
                result.regular_pivots.append(
                    RegularPivot(
                        bar=pivot_bar, price=pl, type="low", confirmed_at=n,
                        time=get_time(pivot_bar), confirmed_time=get_time(n)
                    )
                )
                style = "dashed" if (pl > min_ or prev_os == 0) else "solid"
                add_zigzag_segment(px1, py1, pivot_bar, pl, style)

            py1, px1 = pl, n - length
            os = 0
            max_, min_ = pl, pl

    # ================ barstate.islast projection (final bar only) =========
    if n_bars > 0 and show_miss and px1 is not None:
        n = n_bars - 1
        if n > px1:
            prices: List[float] = []
            prices_x: List[int] = []
            for i in range(0, n - px1):
                idx = n - i
                prices.append(low[idx] if os == 1 else high[idx])
                prices_x.append(idx)

            if prices:
                if os == 1:
                    y = min(prices)
                    ptype: PivotType = "low"
                else:
                    y = max(prices)
                    ptype = "high"
                x = prices_x[prices.index(y)]

                result.projection_point = MissedPivot(
                    bar=x, price=y, type=ptype, confirmed_at=n,
                    time=get_time(x), confirmed_time=get_time(n)
                )
                result.projection_zigzag = Segment(
                    px1, py1, x, y, "dashed", kind="projection",
                    t1=get_time(px1), t2=get_time(x)
                )
                result.projection_ghost_level = Segment(
                    x, y, n, y, kind="projection",
                    t1=get_time(x), t2=get_time(n)
                )

    return result


# --------------------------------------------------------------------------- #
# Incremental Bar-By-Bar Tracker for Live Autotrading Bots
# --------------------------------------------------------------------------- #
@dataclass
class BarEvent:
    """An event emitted on bar close by IncrementalPivotTracker."""
    bar: int
    time: Optional[int]
    confirmed_regular_pivots: List[RegularPivot] = field(default_factory=list)
    confirmed_missed_pivots: List[MissedPivot] = field(default_factory=list)
    new_segments: List[Segment] = field(default_factory=list)


class IncrementalPivotTracker:
    """
    Maintains indicator state across incoming live streaming bars for autotrading.
    Emits events strictly at the bar of confirmation (non-repainting).
    """
    def __init__(self, length: int = 10, show_reg: bool = True, show_miss: bool = True):
        self.length = length
        self.show_reg = show_reg
        self.show_miss = show_miss

        self.highs: List[float] = []
        self.lows: List[float] = []
        self.timestamps: List[int] = []

        self.all_regular_pivots: List[RegularPivot] = []
        self.all_missed_pivots: List[MissedPivot] = []

    def update_bar(self, high: float, low: float, timestamp: Optional[int] = None) -> BarEvent:
        """Call on each newly closed bar."""
        self.highs.append(float(high))
        self.lows.append(float(low))
        if timestamp is not None:
            self.timestamps.append(int(timestamp))

        # Recompute result with current history
        ts = self.timestamps if len(self.timestamps) == len(self.highs) else None
        res = pivot_points_high_low_missed(
            self.highs, self.lows, length=self.length,
            show_reg=self.show_reg, show_miss=self.show_miss,
            timestamps=ts
        )

        n = len(self.highs) - 1
        current_time = self.timestamps[n] if ts else None

        # Filter pivots that were confirmed ON THIS EXACT BAR
        new_reg = [p for p in res.regular_pivots if p.confirmed_at == n]
        new_miss = [m for m in res.missed_pivots if m.confirmed_at == n]

        self.all_regular_pivots.extend(new_reg)
        self.all_missed_pivots.extend(new_miss)

        return BarEvent(
            bar=n,
            time=current_time,
            confirmed_regular_pivots=new_reg,
            confirmed_missed_pivots=new_miss,
        )

    def get_latest_pivots(self) -> PivotResult:
        ts = self.timestamps if len(self.timestamps) == len(self.highs) else None
        return pivot_points_high_low_missed(
            self.highs, self.lows, length=self.length,
            show_reg=self.show_reg, show_miss=self.show_miss,
            timestamps=ts
        )


# --------------------------------------------------------------------------- #
# Demo / self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import random
    import time

    random.seed(7)

    # Synthetic random-walk OHLC
    n_demo_bars = 400
    price = 100.0
    high, low = [], []
    base_time = int(time.time()) - (n_demo_bars * 3600)
    timestamps = [base_time + (i * 3600) for i in range(n_demo_bars)]

    for _ in range(n_demo_bars):
        change = random.uniform(-1.0, 1.0)
        price += change
        bar_high = price + random.uniform(0.1, 1.0)
        bar_low = price - random.uniform(0.1, 1.0)
        high.append(round(bar_high, 4))
        low.append(round(bar_low, 4))

    LENGTH = 10

    result = pivot_points_high_low_missed(
        high, low, length=LENGTH, show_reg=True, show_miss=True, timestamps=timestamps
    )

    print(f"Bars: {n_demo_bars}, length: {LENGTH}")
    print(f"Regular pivots found: {len(result.regular_pivots)}")
    for p in result.regular_pivots[:5]:
        print(f"  bar={p.bar:4d}  {p.type:<4}  {p.price:.4f}  (confirmed at bar {p.confirmed_at}, time={p.time})")

    print(f"\nMissed (ghost) pivots found: {len(result.missed_pivots)}")
    for m in result.missed_pivots[:5]:
        print(f"  bar={m.bar:4d}  {m.type:<4}  {m.price:.4f}  (drawn at bar {m.confirmed_at})")

    if result.projection_point:
        pp = result.projection_point
        print(f"\nLive projection: bar={pp.bar}  {pp.type}  {pp.price:.4f}")

    # Optional visual check
    try:
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(14, 6))
        ax.plot(range(n_demo_bars), high, color="lightgray", linewidth=0.8)
        ax.plot(range(n_demo_bars), low, color="lightgray", linewidth=0.8)

        for seg in result.zigzag:
            ax.plot([seg.x1, seg.x2], [seg.y1, seg.y2],
                     linestyle="--" if seg.style == "dashed" else "-",
                     color="orange", linewidth=1.2)

        for p in result.regular_pivots:
            color = "#ef5350" if p.type == "high" else "#26a69a"
            marker = "v" if p.type == "high" else "^"
            ax.scatter(p.bar, p.price, color=color, marker=marker, s=60, zorder=5)

        for m in result.missed_pivots:
            color = "#ef5350" if m.type == "high" else "#26a69a"
            ax.scatter(m.bar, m.price, facecolors="none", edgecolors=color, s=60, zorder=5)

        if result.projection_point:
            pp = result.projection_point
            color = "#ef5350" if pp.type == "high" else "#26a69a"
            ax.scatter(pp.bar, pp.price, facecolors="none", edgecolors=color, s=80, zorder=5, linewidths=2)

        ax.set_title("Pivot Points High Low & Missed Reversal Levels -- Refined Python Port")
        plt.tight_layout()
        plt.savefig("demo_plot.png", dpi=130)
        print("\nSaved refined demo_plot.png to ./demo_plot.png")
    except ImportError:
        pass