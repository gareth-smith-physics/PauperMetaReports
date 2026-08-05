from __future__ import annotations

import colorsys
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from pauper_meta_reports import DeckRegistry, History, LGSRegistry, NameRegistry, Record, get_collection

# --- Palette (project dataviz skill's validated default - see references/palette.md) ---
CATEGORICAL = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]
SEQUENTIAL_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def _shade(hex_color: str, lightness_delta: float) -> str:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = min(1.0, max(0.0, l + lightness_delta))
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return "#{:02x}{:02x}{:02x}".format(round(r2 * 255), round(g2 * 255), round(b2 * 255))


# 24 colors (8 hue families x 3 lightness tiers) so a chart with many decks doesn't
# have to cycle through only 8 slots. Past 8 series the skill's CVD guarantees no
# longer strictly hold - this trades some of that rigor for visual variety at higher
# deck counts, which is the tradeoff of showing this many series at once.
EXTENDED_CATEGORICAL = CATEGORICAL + [_shade(c, 0.18) for c in CATEGORICAL] + [_shade(c, -0.18) for c in CATEGORICAL]

OTHER_GRAY = "#898781"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRIDLINE = "#404040"  # dark gray, for charts set against a transparent/dark backdrop
SURFACE = "#fcfcfb"

TOP_N_DECKS = 20  # categorical series-count ladder: past ~7, fold the tail into "Other"
Z_68 = 1.0  # ~68.27% CI (+/- 1 SD) under the normal approximation the Wilson formula is built on


def wilson_interval(wins: int, n: int, z: float = Z_68) -> tuple[float, float]:
    """68% Wilson score interval (+/- 1 SD) for a win rate - stays within [0, 1]
    and stable at small sample sizes, unlike a plain normal-approximation interval."""
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _blue_colorscale() -> list[list[float | str]]:
    n = len(SEQUENTIAL_BLUE)
    return [[i / (n - 1), c] for i, c in enumerate(SEQUENTIAL_BLUE)]


@st.cache_data
def load_results() -> pd.DataFrame:
    history = History.load()
    rows = []
    for report in history:
        for result in report:
            if result.player is None or result.deck is None:
                continue  # skipped/unresolved entries are excluded from stats
            rows.append(
                {
                    "date": result.date,
                    "event": result.event,
                    "player": result.player,
                    "deck": result.deck,
                    "wins": result.record.wins,
                    "losses": result.record.losses,
                    "draws": result.record.draws,
                }
            )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["matches"] = df["wins"] + df["losses"] + df["draws"]
    return df


def leaderboard_table(df: pd.DataFrame) -> pd.DataFrame:
    agg = df.groupby("player")[["wins", "losses", "draws", "matches"]].sum().reset_index()
    agg["win_rate"] = agg.apply(lambda r: r["wins"] / r["matches"] if r["matches"] else 0.0, axis=1)

    # Most-played deck per player; ties broken alphabetically for determinism.
    deck_counts = df.groupby(["player", "deck"]).size().reset_index(name="n")
    deck_counts = deck_counts.sort_values(["player", "n", "deck"], ascending=[True, False, True])
    top_deck = deck_counts.groupby("player").first()["deck"]
    agg["top_deck"] = agg["player"].map(top_deck)

    return agg.sort_values(
        by=["wins", "win_rate", "player"],
        ascending=[False, False, True],
    )


def win_rate_table(df: pd.DataFrame, group_col: str, min_matches: int) -> pd.DataFrame:
    agg = df.groupby(group_col)[["wins", "losses", "draws", "matches"]].sum().reset_index()
    agg = agg[agg["matches"] >= min_matches]
    agg["win_rate"] = agg["wins"] / agg["matches"]
    bounds = [wilson_interval(w, n) for w, n in zip(agg["wins"], agg["matches"])]
    agg["ci_lower"] = [b[0] for b in bounds]
    agg["ci_upper"] = [b[1] for b in bounds]
    return agg.sort_values("win_rate")


def win_rate_figure(agg: pd.DataFrame, group_col: str) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=agg["win_rate"],
            y=agg[group_col],
            orientation="h",
            marker=dict(color=agg["win_rate"], colorscale=_blue_colorscale(), cmin=0, cmax=1),
            error_x=dict(
                type="data",
                symmetric=False,
                array=agg["ci_upper"] - agg["win_rate"],
                arrayminus=agg["win_rate"] - agg["ci_lower"],
                color="#ffffff",
                thickness=1.5,
                width=4,
            ),
            customdata=agg[["wins", "losses", "draws", "matches", "ci_lower", "ci_upper"]].values,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Record: %{customdata[0]}-%{customdata[1]}-%{customdata[2]}"
                " (%{customdata[3]} matches)<br>"
                "Win rate: %{x:.1%}<br>"
                "68%% CI: %{customdata[4]:.0%}-%{customdata[5]:.0%}<extra></extra>"
            ),
        )
    )
    fig.add_vline(x=0.5, line=dict(color="red", width=1.5), layer="below")
    fig.update_layout(
        height=max(320, 40 * len(agg) + 140),
        margin=dict(l=10, r=130, t=20, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(range=[0, 1], tickformat=".0%", gridcolor=GRIDLINE, zeroline=False, showgrid=True),
        yaxis=dict(title=None, automargin=True, gridcolor=GRIDLINE, showgrid=True),
        bargap=0.35,
        showlegend=False,
        font=dict(color="#ffffff"),
    )
    return fig


def meta_share_data(df: pd.DataFrame, top_n: int = TOP_N_DECKS):
    counts = df["deck"].value_counts()  # already descending
    share = counts / counts.sum() * 100
    top = share.head(top_n)
    tail = share.iloc[top_n:]

    labels = list(top.index)
    values = list(top.values)
    colors = [EXTENDED_CATEGORICAL[i % len(EXTENDED_CATEGORICAL)] for i in range(len(labels))]
    hover_extra = [None] * len(labels)

    if not tail.empty:
        labels.append("Other")
        values.append(float(tail.sum()))
        colors.append(OTHER_GRAY)
        hover_extra.append(f"{len(tail)} other deck(s)")

    return labels, values, colors, hover_extra, counts


def meta_share_figure(labels, values, colors, hover_extra) -> go.Figure:
    hovertext = [
        f"{lbl}: {val:.1f}%" + (f" ({extra})" if extra else "")
        for lbl, val, extra in zip(labels, values, hover_extra)
    ]
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            marker=dict(colors=colors, line=dict(color=SURFACE, width=2)),
            textinfo="label+percent",
            hovertext=hovertext,
            hoverinfo="text",
            sort=False,
        )
    )
    fig.update_layout(
        height=520,
        margin=dict(l=20, r=20, t=20, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        font=dict(color=TEXT_PRIMARY),
    )
    return fig


def time_bin_edges(start_date, end_date, n_segments: int = 10, min_bin_days: int = 7) -> list:
    """Up to n_segments equal-width time-window boundaries over [start_date, end_date].
    Never makes a window narrower than min_bin_days - a short range gets fewer,
    wider segments instead. Shared by every "evolution over time" chart so they
    all subdivide the selected range identically."""
    span_days = (end_date - start_date).days
    n_segments = min(n_segments, max(1, span_days // min_bin_days))

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1)  # exclusive upper edge, covers end_date fully
    return list(pd.date_range(start=start_ts, end=end_ts, periods=n_segments + 1))


def meta_share_evolution_data(
    df: pd.DataFrame,
    start_date,
    end_date,
    n_segments: int = 10,
    min_bin_days: int = 7,
    top_n: int = TOP_N_DECKS,
):
    """Compute each deck's meta share within each time_bin_edges() window.
    A bin with zero total results gets NaN (not 0%) for every deck, so a
    window with no event at all reads as a gap to bridge across rather than
    a real "nobody played anything" data point."""
    edges = time_bin_edges(start_date, end_date, n_segments, min_bin_days)
    n_segments = len(edges) - 1

    dated = df.copy()
    dated["date_ts"] = pd.to_datetime(dated["date"])
    dated["segment"] = pd.cut(dated["date_ts"], bins=edges, labels=False, right=False, include_lowest=True)

    overall_counts = df["deck"].value_counts()
    top_decks = list(overall_counts.head(top_n).index)
    has_other = len(overall_counts) > top_n
    deck_order = top_decks + (["Other"] if has_other else [])

    rows = []
    for seg in range(n_segments):
        seg_rows = dated[dated["segment"] == seg]
        counts = seg_rows["deck"].value_counts()
        total = counts.sum()
        for deck in top_decks:
            share = (counts.get(deck, 0) / total * 100) if total else float("nan")
            rows.append({"date": edges[seg], "deck": deck, "share": share})
        if has_other:
            other_count = counts[~counts.index.isin(top_decks)].sum() if not counts.empty else 0
            other_share = (other_count / total * 100) if total else float("nan")
            rows.append({"date": edges[seg], "deck": "Other", "share": other_share})

    return pd.DataFrame(rows), deck_order, list(edges)


def meta_share_evolution_figure(
    evolution_df: pd.DataFrame, deck_order: list[str], edges: list
) -> go.Figure:
    fig = go.Figure()
    last_edge = edges[-1]
    for i, deck in enumerate(deck_order):
        color = OTHER_GRAY if deck == "Other" else EXTENDED_CATEGORICAL[i % len(EXTENDED_CATEGORICAL)]
        # Drop NaN-share (totally empty) bins outright rather than plotting
        # them as 0 - Plotly's own linear interpolation then draws a single
        # straight line straight from the last real bin to the next one,
        # bridging the empty window smoothly instead of dipping to 0% and
        # back up.
        d = evolution_df[(evolution_df["deck"] == deck) & evolution_df["share"].notna()].sort_values("date")
        # Each row's date is its bin's *start*. Repeat the last bin's value out to
        # the final boundary (step-shaped) so the fill reaches the true right edge
        # instead of stopping one bin-width short of it.
        x = list(d["date"]) + [last_edge]
        y = list(d["share"]) + [d["share"].iloc[-1]]
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                name=deck,
                mode="lines",
                stackgroup="one",
                line=dict(width=0.5, color=color),
                fillcolor=color,
                hoverinfo="skip",  # purely visual - the marker-grid traces below own all hover
            )
        )

    for edge in edges[:-1]:
        # Skip a bin that's genuinely empty - the fill bridges straight
        # through it now, so a separator line there would mark a boundary
        # that no longer visually exists.
        if evolution_df[evolution_df["date"] == edge]["share"].isna().all():
            continue
        fig.add_vline(x=edge, line=dict(color="#000000", width=1), layer="above")

    # Per-deck hover, for hovering a shaded band strictly *between* bin edges.
    # hoveron="fills" doesn't work for this: Plotly anchors a fill's tooltip
    # to one fixed reference point on the trace, not to the cursor, so it
    # doesn't track position within the shape at all. Instead, build a dense
    # grid of invisible marker points that actually covers each deck's
    # stacked area - interpolating its share linearly between the two
    # bracketing edges (matching the fill's own linear interpolation) and
    # stacking decks in draw order to get each one's true bottom/top at that
    # x - so "closest" hovermode finds a real nearby point wherever the
    # cursor is and names the right deck. Samples are kept strictly inside
    # each bin (never exactly on an edge) so they never compete with the
    # per-edge combined-box trace below for priority right on a line.
    def _share_points(deck: str) -> list:
        # Real (non-NaN) (x, share) points only, sorted - same set the fill
        # trace above draws, so hover interpolation matches what's visually
        # shown, including bridging straight across any empty bins.
        d = evolution_df[(evolution_df["deck"] == deck) & evolution_df["share"].notna()].sort_values("date")
        points = list(zip(d["date"], d["share"]))
        points.append((last_edge, d["share"].iloc[-1]))
        return points

    def _lerp(points: list, x_ts) -> float:
        if x_ts <= points[0][0]:
            return points[0][1]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= x_ts <= x1:
                return y0 if x1 == x0 else y0 + (y1 - y0) * (x_ts - x0) / (x1 - x0)
        return points[-1][1]

    deck_series = {deck: _share_points(deck) for deck in deck_order}
    n_x_per_bin = 8
    n_y_samples = 6
    grid_x, grid_y, grid_text = [], [], []
    for x0, x1 in zip(edges, edges[1:]):
        for xs in range(n_x_per_bin):
            x_ts = x0 + (x1 - x0) * ((xs + 0.5) / n_x_per_bin)  # strictly inside (x0, x1)
            cumulative = 0.0
            for deck in deck_order:
                share = _lerp(deck_series[deck], x_ts)
                bottom, cumulative = cumulative, cumulative + share
                if share <= 0.0:
                    continue
                for ys in range(n_y_samples):
                    grid_x.append(x_ts)
                    grid_y.append(bottom + share * ((ys + 0.5) / n_y_samples))
                    grid_text.append(deck)

    fig.add_trace(
        go.Scatter(
            x=grid_x,
            y=grid_y,
            mode="markers",
            marker=dict(size=1, opacity=0),
            hoverinfo="text",
            hovertext=[f"<b>{deck}</b>" for deck in grid_text],
            showlegend=False,
        )
    )

    # Combined per-bin stats box, at each bin edge only: a column of stacked,
    # invisible marker points spanning the full height of the chart at that
    # edge's x, all sharing one hovertext listing every deck's share in that
    # bin (0% decks omitted). Added last so it wins any tie against the
    # per-deck grid right on the line. Skipped entirely for a genuinely empty
    # bin - the fill no longer treats that edge as a distinct region (it's
    # bridged straight through), so a hover column there would have nothing
    # useful to say and would only crowd out the per-deck grid's tooltip
    # right where it's bridging across the gap.
    hover_x, hover_y, hover_text = [], [], []
    n_edge_y_samples = 26
    for edge in edges[:-1]:
        bin_rows = evolution_df[evolution_df["date"] == edge]
        lines = [
            f"<b>{row.deck}</b>: {row.share:.1f}%" for row in bin_rows.itertuples() if row.share > 0.0
        ]
        if not lines:
            continue
        text = "<br>".join(lines)
        for step in range(n_edge_y_samples):
            hover_x.append(edge)
            hover_y.append(step * 100 / (n_edge_y_samples - 1))
            hover_text.append(text)

    fig.add_trace(
        go.Scatter(
            x=hover_x,
            y=hover_y,
            mode="markers",
            marker=dict(size=8, opacity=0),
            hoverinfo="text",
            hovertext=hover_text,
            showlegend=False,
        )
    )

    fig.update_layout(
        height=680,
        margin=dict(l=10, r=10, t=50, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(gridcolor=GRIDLINE, showgrid=True, title=None, range=[edges[0], edges[-2]]),
        yaxis=dict(range=[0, 100], ticksuffix="%", gridcolor=GRIDLINE, showgrid=True, title=None),
        legend=dict(orientation="h", yanchor="bottom", y=1.1, xanchor="left", x=0),
        hovermode="closest",
        font=dict(color=TEXT_PRIMARY),
    )
    return fig


def deck_win_rate_evolution_data(
    df: pd.DataFrame,
    deck: str,
    start_date,
    end_date,
    n_segments: int = 10,
    min_bin_days: int = 7,
) -> tuple[pd.DataFrame, list]:
    """One deck's win rate (+ 68% CI) within each time_bin_edges() window.
    A bin with zero matches gets NaN (not 0%) for win_rate/ci_lower/ci_upper,
    so the line and band show a gap there instead of a false "lost everything"."""
    edges = time_bin_edges(start_date, end_date, n_segments, min_bin_days)
    n_segments = len(edges) - 1

    dated = df[df["deck"] == deck].copy()
    dated["date_ts"] = pd.to_datetime(dated["date"])
    dated["segment"] = pd.cut(dated["date_ts"], bins=edges, labels=False, right=False, include_lowest=True)

    rows = []
    for seg in range(n_segments):
        seg_rows = dated[dated["segment"] == seg]
        wins = int(seg_rows["wins"].sum())
        matches = int(seg_rows["matches"].sum())
        if matches:
            win_rate = wins / matches
            ci_lower, ci_upper = wilson_interval(wins, matches)
        else:
            win_rate = float("nan")
            ci_lower, ci_upper = float("nan"), float("nan")
        rows.append(
            {
                "date": edges[seg],
                "wins": wins,
                "matches": matches,
                "win_rate": win_rate,
                "ci_lower": ci_lower,
                "ci_upper": ci_upper,
            }
        )

    return pd.DataFrame(rows), edges


def deck_win_rate_evolution_figure(evo_df: pd.DataFrame, edges: list, deck: str) -> go.Figure:
    line_color = SEQUENTIAL_BLUE[3]  # magnitude-over-time -> sequential blue, not deck-identity color
    band_color = "rgba(57, 135, 229, 0.25)"
    last_edge = edges[-1]

    # Step-shaped, closed out to the final boundary - same convention as the
    # meta share evolution chart, so a bin's value holds flat across its full width.
    x = list(evo_df["date"]) + [last_edge]
    win_rate = list(evo_df["win_rate"]) + [evo_df["win_rate"].iloc[-1]]
    ci_lower = list(evo_df["ci_lower"]) + [evo_df["ci_lower"].iloc[-1]]
    ci_upper = list(evo_df["ci_upper"]) + [evo_df["ci_upper"].iloc[-1]]
    wins = list(evo_df["wins"]) + [evo_df["wins"].iloc[-1]]
    matches = list(evo_df["matches"]) + [evo_df["matches"].iloc[-1]]

    fig = go.Figure()

    # Two-line "tonexty" band rather than a single forward+reversed polygon:
    # this handles the NaN gaps from empty bins correctly (each line just
    # breaks at the gap, same as the win-rate line), where a single closed
    # polygon path would render oddly across a break. mode="lines" is
    # explicit on both - Plotly defaults small scatter traces to
    # "lines+markers", which is what was drawing the stray dots.
    fig.add_trace(
        go.Scatter(
            x=x,
            y=ci_lower,
            mode="lines",
            line=dict(width=0),
            hoverinfo="skip",
            showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=ci_upper,
            mode="lines",
            line=dict(width=0),
            fill="tonexty",
            fillcolor=band_color,
            hoverinfo="skip",
            showlegend=False,
        )
    )

    fig.add_trace(
        go.Scatter(
            x=x,
            y=win_rate,
            mode="lines",
            line=dict(width=2, color=line_color),
            name=deck,
            customdata=list(zip(wins, matches, ci_lower, ci_upper)),
            hovertemplate=(
                f"<b>{deck}</b><br>"
                "Win rate: %{y:.1%}<br>"
                "%{customdata[0]:.0f} wins / %{customdata[1]:.0f} matches<br>"
                "CI: %{customdata[2]:.0%}-%{customdata[3]:.0%}<extra></extra>"
            ),
        )
    )

    for edge in edges[:-1]:
        fig.add_vline(x=edge, line=dict(color="#ffffff", width=1), layer="above")

    fig.update_layout(
        height=520,
        margin=dict(l=10, r=10, t=40, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(gridcolor=GRIDLINE, showgrid=True, title=None, range=[edges[0], edges[-2]]),
        yaxis=dict(range=[0, 1], tickformat=".0%", gridcolor=GRIDLINE, showgrid=True, title=None),
        showlegend=False,
        hovermode="x",
        font=dict(color=TEXT_PRIMARY),
    )
    return fig


def check_password() -> bool:
    """Simple shared-password gate. The password lives in Streamlit secrets
    (.streamlit/secrets.toml locally; the Cloud UI's Secrets panel when
    deployed) - never in the repo, private or not."""
    if st.session_state.get("authenticated"):
        return True

    password = st.text_input("Password", type="password")

    try:
        expected = st.secrets.get("password")
    except StreamlitSecretNotFoundError:
        expected = None

    if not expected:
        st.error(
            "No password configured - set `password` in .streamlit/secrets.toml "
            "(copy .streamlit/secrets.toml.example)."
        )
        return False

    if not password:
        return False

    if password == expected:
        st.session_state["authenticated"] = True
        return True

    st.error("Incorrect password.")
    return False


st.set_page_config(page_title="Pauper Meta Reports", layout="wide")
st.title("Vancouver Pauper Meta")

if not check_password():
    st.stop()

df = load_results()

if df.empty:
    st.info("No meta report data yet - run the parser or Discord sync first.")
    st.stop()

min_date, max_date = df["date"].min(), df["date"].max()

with st.sidebar:
    st.header("Filters")
    date_range = st.date_input(
        "Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date
    )
    #min_matches = st.slider(
    #    "Minimum matches played",
    #    min_value=1,
    #    max_value=10,
    #    value=2,
    #    help="Hide players/decks with too few matches for a win rate to be meaningful.",
    #)

if not isinstance(date_range, tuple) or len(date_range) != 2:
    st.stop()  # mid-selection (only the start of the range picked so far)

start_date, end_date = date_range
filtered = df[(df["date"] >= start_date) & (df["date"] <= end_date)]

if filtered.empty:
    st.warning("No results in this date range.")
    st.stop()

st.caption(
    f"{len(filtered)} results across {filtered['date'].nunique()} meta reports, "
    f"{start_date} to {end_date}"
)

TAB_LABELS = [
    "Meta Reports",
    "Leaderboard",
    "Player History",
    "Meta Share",
    "Deck Win Rates",
    "Meta Share Evolution",
    "Deck Win Rate Evolution",
    "Unresolved",
]

# st.tabs() has no persistence: it silently snaps back to the first tab on any
# rerun triggered by a widget inside a non-first tab (e.g. the deck/player
# selectbox), since it isn't backed by session_state. segmented_control is a
# real stateful widget - its key survives reruns - so the active view stays put.
active_tab = st.segmented_control(
    "View", TAB_LABELS, default=TAB_LABELS[0], key="active_tab", label_visibility="collapsed"
) or TAB_LABELS[0]  # guards against the single-select deselect-to-None case

if active_tab == "Meta Reports":
    st.subheader("Meta Reports")

    report_keys = filtered[["date", "event"]].drop_duplicates().sort_values("date", ascending=False)

    for _, key in report_keys.iterrows():
        report_rows = filtered[(filtered["date"] == key["date"]) & (filtered["event"] == key["event"])]
        records = [Record(w, l, d) for w, l, d in zip(report_rows["wins"], report_rows["losses"], report_rows["draws"])]

        report_table = report_rows.assign(
            record=[str(r) for r in records],
            score=[r.score for r in records],
        ).sort_values("score", ascending=False)

        # Trophy for an undefeated record (no losses, no draws, at least one win).
        undefeated = (
            (report_table["losses"] == 0) & (report_table["draws"] == 0) & (report_table["wins"] > 0)
        )
        report_table["player"] = report_table["player"].mask(undefeated, "🏆 " + report_table["player"])

        st.markdown(f"**{key['date']}** — {key['event']} — {len(report_table)} players")
        st.dataframe(
            report_table[["player", "deck", "record"]],
            width="stretch",
            hide_index=True,
            column_config={
                "player": st.column_config.Column("Player Name"),
                "deck": st.column_config.Column("Deck"),
                "record": st.column_config.Column("Record"),
            },
        )

if active_tab == "Leaderboard":
    st.subheader("Leaderboard")
    #st.caption("Ranked by wins, then win rate, then alphabetically.")

    board = leaderboard_table(filtered)
    board_display = board.assign(win_rate=(board["win_rate"] * 100).round(1)).rename(
        columns={"matches": "games", "win_rate": "win_rate_%"}
    ).reset_index(drop=True)
    board_display.insert(0, "rank", board_display.index + 1)
    st.dataframe(
        board_display[["rank", "player", "top_deck", "wins", "games", "win_rate_%"]],
        width="stretch",
        hide_index=True,
        column_config={
        "rank": st.column_config.Column("Rank"),
        "player": st.column_config.Column("Player Name"),
        "top_deck": st.column_config.Column("Top Deck"),
        "wins": st.column_config.Column("Total Wins"),
        "games": st.column_config.Column("Matches Played"),
        "win_rate_%": st.column_config.NumberColumn(
            "Win Rate",
            format="%.1f%%",  # Automatically adds the % sign to the values
            ),
        },
    )

if active_tab == "Player History":
    st.subheader("Player History")

    players = sorted(filtered["player"].unique())
    selected_player = st.selectbox("Search for a player", players)

    if selected_player:
        board = leaderboard_table(filtered)
        totals = board[board["player"] == selected_player].iloc[0]
        player_rows = filtered[filtered["player"] == selected_player].sort_values("date", ascending=False)
        events_attended = player_rows[["date", "event"]].drop_duplicates().shape[0]

        col1, col2, col3, col4, col5, col6 = st.columns([1, 1, 1, 1, 1, 3])
        col1.metric("Events", events_attended)
        col2.metric("Games", int(totals["matches"]))
        col3.metric("Wins", int(totals["wins"]))
        col4.metric("Ties", int(totals["draws"]))
        col5.metric("Win Rate", f"{totals['win_rate'] * 100:.1f}%")
        col6.metric("Top Deck", totals["top_deck"])

        st.markdown("**Event History**")
        history_table = player_rows.assign(
            record=[
                str(Record(w, l, d))
                for w, l, d in zip(player_rows["wins"], player_rows["losses"], player_rows["draws"])
            ]
        )[["date", "event", "deck", "record"]]
        st.dataframe(
            history_table,
            width="stretch",
            hide_index=True,
            column_config={
                "date": st.column_config.Column("Date"),
                "event": st.column_config.Column("Event"),
                "deck": st.column_config.Column("Deck"),
                "record": st.column_config.Column("Record"),
            },
        )

if active_tab == "Meta Share":
    st.subheader("Deck Meta Share")
    #st.caption(f"Share of all results in range. Top {TOP_N_DECKS} decks shown individually; the rest are folded into “Other”.")

    venues = ["All"] + sorted(filtered["event"].unique())
    selected_venue = st.selectbox("Venue", venues, key="meta_share_venue")
    venue_filtered = filtered if selected_venue == "All" else filtered[filtered["event"] == selected_venue]

    filtered_known_decks = venue_filtered[venue_filtered["deck"] != "Unknown"]
    labels, values, colors, hover_extra, deck_counts = meta_share_data(filtered_known_decks)
    st.plotly_chart(meta_share_figure(labels, values, colors, hover_extra), width="stretch")
    
    table = (
        deck_counts.rename("count")
        .to_frame()
        .assign(share_pct=lambda d: (d["count"] / d["count"].sum() * 100).round(1))
        .reset_index(names="deck")
    )
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={
            "share_pct": st.column_config.NumberColumn("Share %", format="%.1f%%"),
            "deck": st.column_config.Column("Deck"),
            "count": st.column_config.Column("Total Matches"),
        },
    )

if active_tab == "Deck Win Rates":
    st.subheader("Deck Win Rate")
    #st.caption(
    #    "Win rate = wins ÷ (wins + losses + draws), aggregated across every player who played the deck. "
    #    "Error bars are a 68% confidence interval (+/- 1 SD, Wilson score) - wide bars mean too few matches to be sure."
    #)

    deck_agg = win_rate_table(filtered[filtered["deck"] != "Unknown"], "deck", 1)
    if deck_agg.empty:
        st.info("No decks meet the minimum-matches filter in this range.")
    else:
        st.plotly_chart(win_rate_figure(deck_agg, "deck"), width="stretch")
        #with st.expander("View data"):
        table = deck_agg.assign(
            win_rate=(deck_agg["win_rate"] * 100).round(1),
            ci_lower=(deck_agg["ci_lower"] * 100).round(1),
            ci_upper=(deck_agg["ci_upper"] * 100).round(1),
        ).rename(columns={"win_rate": "win_rate_%", "ci_lower": "ci_lower_%", "ci_upper": "ci_upper_%"})
        st.dataframe(table, width="stretch", hide_index=True, column_config={
            "win_rate_%": st.column_config.NumberColumn("Win Rate %", format="%.1f%%"),
            "ci_lower_%": st.column_config.NumberColumn("CI Lower %", format="%.1f%%"),
            "ci_upper_%": st.column_config.NumberColumn("CI Upper %", format="%.1f%%"),
            "deck": st.column_config.Column("Deck"),
            "wins": st.column_config.Column("Wins"),
            "losses": st.column_config.Column("Losses"),
            "draws": st.column_config.Column("Draws"),
            "matches": st.column_config.Column("Matches"),
        })

if active_tab == "Meta Share Evolution":
    st.subheader("Meta Share Evolution")
    #st.caption(
    #    "The selected date range is split into 10 equal-length time windows; each band's "
    #    "thickness is that deck's share of results within that window."
    #)

    evolution_df, deck_order, edges = meta_share_evolution_data(filtered, start_date, end_date)
    st.plotly_chart(meta_share_evolution_figure(evolution_df, deck_order, edges), width="stretch")

if active_tab == "Deck Win Rate Evolution":
    st.subheader("Deck Win Rate Evolution")
    st.caption(
        "Same time subdivisions as Meta Share Evolution. Shaded band is a 68% "
        "confidence interval (+/- 1 SD, Wilson score) - wide bands mean too few matches to be sure."
    )

    evo_decks = sorted(filtered["deck"].unique())
    selected_deck = st.selectbox("Select a deck", evo_decks)

    if selected_deck:
        deck_evo_df, deck_evo_edges = deck_win_rate_evolution_data(filtered, selected_deck, start_date, end_date)
        st.plotly_chart(
            deck_win_rate_evolution_figure(deck_evo_df, deck_evo_edges, selected_deck), width="stretch"
        )

        deck_evo_table = deck_evo_df.assign(
            date=deck_evo_df["date"].dt.strftime("%Y-%m-%d"),
            win_rate=(deck_evo_df["win_rate"] * 100).round(1),
            ci_lower=(deck_evo_df["ci_lower"] * 100).round(1),
            ci_upper=(deck_evo_df["ci_upper"] * 100).round(1),
        ).rename(columns={"date": "Time Bin", "win_rate": "win_rate_%", "ci_lower": "ci_lower_%", "ci_upper": "ci_upper_%"})
        st.dataframe(
            deck_evo_table,
            width="stretch",
            hide_index=True,
            column_config={
                "Time Bin": st.column_config.Column("Time Bin"),
                "wins": st.column_config.Column("Wins"),
                "matches": st.column_config.Column("Matches"),
                "win_rate_%": st.column_config.NumberColumn("Win Rate %", format="%.1f%%"),
                "ci_lower_%": st.column_config.NumberColumn("CI Lower %", format="%.1f%%"),
                "ci_upper_%": st.column_config.NumberColumn("CI Upper %", format="%.1f%%"),
            },
        )

if active_tab == "Unresolved":
    st.subheader("Unresolved deck, player, and venue names")
    st.caption(
        "Ambiguous names/decks the Discord parser couldn't confidently match on its own, plus "
        "reports whose venue couldn't be determined from the message text. Resolving one here "
        "updates the registry and retroactively fills in any past results that were left "
        "unknown or under a placeholder venue because of it."
    )

    unresolved_collection = get_collection("unresolved")
    pending_items = list(unresolved_collection.find({}).sort("date", -1))

    if not pending_items:
        st.info("Nothing pending review.")

    # Loaded once and reused across items, rather than re-querying MongoDB
    # per pending item.
    name_registry_for_review = NameRegistry()
    deck_registry_for_review = DeckRegistry()
    lgs_registry_for_review = LGSRegistry()

    for item in pending_items:
        registry_kind = item["registry"]  # "names", "decks", or "lgs"

        if registry_kind == "lgs":
            report_date_str = item["raw"]
            placeholder_event = item.get("event")
            snippet = item.get("message_snippet", "")

            with st.form(key=f"resolve_lgs_{item['_id']}"):
                st.markdown(
                    f"Unknown venue for the report on **{report_date_str}** "
                    f"-- currently recorded as **{placeholder_event}**"
                )
                with st.expander("Message text"):
                    st.text(snippet)

                lgs_options = sorted(e.canonical for e in lgs_registry_for_review.entries)
                selected_lgs = st.selectbox(
                    "Existing LGS",
                    lgs_options,
                    index=None,
                    placeholder="Select an existing LGS...",
                    key=f"select_lgs_{item['_id']}",
                    label_visibility="collapsed",
                )
                new_lgs_name = st.text_input(
                    "Or type a new LGS name", key=f"new_lgs_{item['_id']}", placeholder="Or type a new LGS name..."
                )
                submitted = st.form_submit_button("Resolve")

                if submitted:
                    typed = new_lgs_name.strip()
                    chosen = typed or selected_lgs
                    if not chosen:
                        st.warning("Select an existing LGS or type a new one before resolving.")
                    else:
                        if typed:
                            lgs_registry_for_review.add_canonical(typed)
                        report_date_obj = date.fromisoformat(report_date_str)
                        updated_count = History.load().backfill_event(report_date_obj, placeholder_event, chosen)
                        unresolved_collection.delete_one({"_id": item["_id"]})
                        load_results.clear()
                        st.success(
                            f"Resolved venue for {report_date_str} -> **{chosen}** "
                            f"({updated_count} result(s) updated)."
                        )
                        st.rerun()
            continue

        label = "player" if registry_kind == "names" else "deck"
        registry = name_registry_for_review if registry_kind == "names" else deck_registry_for_review
        raw = item["raw"]
        candidate = item.get("candidate")
        score = item.get("score") or 0.0
        matched_alias = item.get("matched_alias")

        with st.form(key=f"resolve_{item['_id']}"):
            st.markdown(f"Unknown {label} name: **{raw}** -- Seen {item['date']} @ {item['event']}")
            if candidate:
                header = f"Closest match: **{candidate}**"
                if matched_alias and matched_alias.lower() != candidate.lower():
                    header += f' ("{matched_alias}")'
                st.markdown(header)

            unknown_label = "Unknown deck"
            select_label = f"Select a different existing {label}"
            options = []
            if candidate:
                options.append(f"Same as {candidate}")
            options.append(f"New {label}")
            if registry_kind == "decks":
                options.append(unknown_label)
            options.append(select_label)

            choice = st.radio("Decision", options, key=f"choice_{item['_id']}", label_visibility="collapsed")
            existing_options = sorted(e.canonical for e in registry.entries)
            selected_existing = st.selectbox(
                f"Existing {label}",
                existing_options,
                index=None,
                placeholder=f"Select an existing {label}...",
                key=f"select_{item['_id']}",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button("Resolve")

            if submitted:
                def synthetic_ask(_raw: str, _candidate: str | None, _score: float, _matched_alias: str | None) -> str:
                    if candidate and choice == f"Same as {candidate}":
                        return "y"
                    if choice == select_label:
                        return selected_existing or ""
                    if choice == unknown_label:
                        # Typed-correction path, not "y"/"n": resolves (or creates,
                        # the first time) a shared "Unknown" catch-all deck, and
                        # aliases this raw text to it so the same joke/unparseable
                        # name auto-resolves next time instead of asking again.
                        return "Unknown"
                    return "n"

                canonical = registry.resolve(raw, ask=synthetic_ask)

                if canonical is None:
                    st.warning("Select an existing entry before resolving, or pick one of the other options.")
                else:
                    field = "player" if registry_kind == "names" else "deck"
                    raw_field = "raw_player" if registry_kind == "names" else "raw_deck"
                    updated_count = History.load().backfill(field, raw_field, raw, canonical)

                    unresolved_collection.delete_one({"_id": item["_id"]})
                    load_results.clear()
                    st.success(f"Resolved **{raw}** -> **{canonical}** ({updated_count} past result(s) updated).")
                    st.rerun()
