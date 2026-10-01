"""
UNDERCUTTING VALIDATION (B3 / UMDF MBO)
=======================================
Purpose: count how many "undercutting" events REALLY happened, and how many new
orders did NOT change the spread length.

Why this script exists
----------------------
`undercutting_analise.py` infers top-of-book by forward-filling every bid/ask
price it sees and then shifting one row. On an MBO (market-by-order) feed that is
not the top of book: every order in the book -- including deep ones -- shows up
as an entry, `md_price_level` is always NULL, and deletions are never applied.
So the "L1" series it plots is not L1 at all.

Here the book is actually replayed, order by order, in `rpt_seq` order:
  * md_update_action 0 = New    -> insert order
  * md_update_action 1 = Change -> move/resize order (old price from order_id map)
  * md_update_action 2 = Delete -> remove order
Best bid / best ask are maintained exactly, so the spread before and after every
single event is known and each new order can be classified by what it did to the
spread -- and then cross-checked against the measured spread change.

Classification of a NEW order (side = its own side, opposite = other side):
  undercut      : price strictly better than best on its own side AND strictly
                  inside the spread -> spread really narrows. THE REAL THING.
  join_best     : price == best on its own side -> queue joining, spread UNCHANGED
  behind_best   : price worse than best on its own side -> depth, spread UNCHANGED
  aggressive    : price at or through the opposite best -> locks/crosses the book
                  (matches immediately; not an undercut)
  no_reference  : one of the two sides is empty -> spread undefined

Outputs
-------
  output_data/microstructure/undercut_valid_events_<sid>_<YYYYMMDD>.parquet  (event cache)
  output_data/microstructure/undercut_valid_1m_<sid>.parquet                 (per-minute panel)
  output_data/microstructure/undercut_valid_report_<sid>.csv                 (per-day validation)
  painel_undercutting_validado.png                                          (dashboard)
"""

import os
import sys
import warnings
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import polars as pl

warnings.filterwarnings("ignore")

# ==============================================================================
# CONFIG
# ==============================================================================
TARGET_SECURITY_ID = 200001274203          # WIN (mini index future)
TICK_SIZE = 5.0                            # WIN price increment, in points
BASE_INPUT_DIR = Path("/share/fgv-quant/market-data/parquet/2025/09/")
OUTPUT_DIR = Path(__file__).resolve().parent / "output_data" / "microstructure"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ARQ_PANEL = OUTPUT_DIR / f"undercut_valid_1m_{TARGET_SECURITY_ID}.parquet"
ARQ_REPORT = OUTPUT_DIR / f"undercut_valid_report_{TARGET_SECURITY_ID}.csv"
IMG_PANEL = Path(__file__).resolve().parent / "painel_undercutting_validado.png"

MAX_DAY_WORKERS = 6                        # day processes in parallel
POLARS_THREADS_PER_WORKER = 4

# event side codes
BID, ASK, TRADE = 0, 1, 2
# md_update_action codes
A_NEW, A_CHANGE, A_DELETE = 0, 1, 2

# per-minute accumulator columns
COLS = [
    "new_bid", "new_ask",
    "undercut_buy_count", "undercut_sell_count",
    "join_best_buy", "join_best_sell",
    "behind_best_buy", "behind_best_sell",
    "aggressive_buy", "aggressive_sell",
    "no_reference_buy", "no_reference_sell",
    "spread_narrowed", "spread_unchanged", "spread_widened_by_new",
    "undercut_ticks_sum",
    # undercuts arriving right after a SAME-SIDE trade: these may be the passive
    # residual of a partly-filled aggressive order rather than fresh passive
    # competition. The feed carries no order_id on trades, so the two cannot be
    # told apart -- this counts the ambiguous ones instead of guessing.
    "undercut_maybe_residual", "undercut_resid_buy", "undercut_resid_sell",
    # thesis (Appendix A) 5-min inputs: message count, midpoint moments and
    # spread sampled on EVERY book event (not only on new orders)
    "msg_total", "mid_sum", "mid_sq", "mid_obs",
    # PROTOCOL 3.1/3.2 -- volume-weighted measure, in CONTRACTS not order counts
    "uc_strict_vol_bid", "uc_strict_vol_ask",
    "uc_amb_vol_bid", "uc_amb_vol_ask",
    "new_vol_bid", "new_vol_ask",
    # PROTOCOL 3.5 -- microprice sampling
    "mp_obs",
    # ---- SUBSTITUTION TEST: the two dimensions left open when the tick
    # forecloses price competition. TIMING = how fast touch liquidity is
    # cancelled; QUANTITY = how much depth sits at, is added to, or is
    # withdrawn from the touch.
    "touch_cancel_n_bid", "touch_cancel_n_ask",
    "touch_life_bid", "touch_life_ask",              # ms, summed
    "touch_depth_bid", "touch_depth_ask", "touch_depth_obs",
    "touch_wd_vol_bid", "touch_wd_vol_ask",          # volume cancelled at touch
    # F1 placebo: cancellations AWAY from the touch. Those orders are not
    # exposed to being picked off, so they cannot be a defensive response.
    "deep_wd_vol_bid", "deep_wd_vol_ask",
    # ---- SPEC §2.2/2.3  queue position at cancellation -------------------
    "fq_wd_bid", "fq_wd_ask",        # volume cancelled with QP <= 0.20
    "bq_wd_bid", "bq_wd_ask",        # volume cancelled with QP  > 0.80
    "qp_sum_bid", "qp_sum_ask",      # sum of QP, for the mean
    "qp_n_bid", "qp_n_ask",
    # ---- SPEC §2.1  price undercutting RUNS ------------------------------
    "ur_run_n_bid", "ur_run_n_ask", "ur_run_len_bid", "ur_run_len_ask",
    # ---- SPEC §2.4  strategic queue cancellation RUNS --------------------
    "qcr_n_bid", "qcr_n_ask", "qcr_len_bid", "qcr_len_ask",
    "touch_add_vol_bid", "touch_add_vol_ask",        # volume added at touch
    "spread_all_sum", "spread_all_obs",
    # CHANGE events (algos repricing a resting order). Unlike a new order these
    # can widen the spread too, so they are classified by the measured delta.
    # WHERE a new order lands: distance in ticks from the best price on its OWN
    # side. 0 = at the touch, positive = behind it. Orders inside the spread are
    # the undercut buckets above, so these sum with them to every new order.
    "depth_0", "depth_1", "depth_2", "depth_3", "depth_4", "depth_5",
    "depth_6_10", "depth_11_20", "depth_21_50", "depth_51_100", "depth_100p",
    "depth_ticks_sum", "depth_obs",
    # AGGRESSIVE ORDERS that hit the book. One marketable order can consume many
    # resting orders and so print many trades; consecutive prints sharing a
    # timestamp AND an aggressor side are grouped back into one incoming order.
    "sweep_buy", "sweep_sell", "sweep_prints_max",
    "reprice_total", "reprice_narrow_buy", "reprice_narrow_sell",
    "reprice_widen", "reprice_neutral", "reprice_size_only", "reprice_ticks_sum",
    "deletes_widening_spread",
    "new_orders_in_locked_book",
    "trade_count", "trade_volume",
    "spread_ticks_sum", "spread_obs",
    "best_bid_size_sum", "best_ask_size_sum",
    # raw spread-length distribution: 1, 2, 3, 4+ ticks
    "spread_ms_1", "spread_ms_2", "spread_ms_3", "spread_ms_4p",
    "spread_n_1", "spread_n_2", "spread_n_3", "spread_n_4p",
    "spread_ms_dropped",
    # naive heuristic reproduction (the logic of undercutting_analise.py)
    "naive_undercut_buy", "naive_undercut_sell",
]
C = {name: i for i, name in enumerate(COLS)}
NCOL = len(COLS)


# ==============================================================================
# PART 1: EVENT EXTRACTION (one compact parquet per day, cached)
# ==============================================================================
def _events_path(date_str: str, sid: int = None) -> Path:
    sid = TARGET_SECURITY_ID if sid is None else sid
    return OUTPUT_DIR / f"undercut_valid_events_{sid}_{date_str}.parquet"


def extrair_eventos_dia(day_dir: Path, sid: int = None) -> Path | None:
    """Flatten one day of MBO messages into the minimal event stream we replay.

    Kept columns: rpt_seq (replay order), sending_time (event clock),
    side, action, order_id, price, size.
    """
    sid = TARGET_SECURITY_ID if sid is None else sid
    date_str = f"{day_dir.parent.parent.name}{day_dir.parent.name}{day_dir.name}"
    out = _events_path(date_str, sid)
    if out.exists():
        return out

    files = [str(p) for p in sorted(day_dir.rglob("*.parquet"))]
    if not files:
        return None

    e = pl.col("md_entries")
    px = e.struct.field("md_entry_px")

    lf = (
        pl.scan_parquet(files)
        .explode("md_entries")
        .with_columns(
            sec_id=pl.coalesce([e.struct.field("security_id"), pl.col("security_id")]),
            entry_type=e.struct.field("md_entry_type"),
            action=e.struct.field("md_update_action"),
            order_id=e.struct.field("order_id"),
            mantissa=px.struct.field("mantissa"),
            exponent=px.struct.field("exponent"),
            size=e.struct.field("md_entry_size"),
            rpt=pl.coalesce([e.struct.field("rpt_seq"), pl.col("rpt_seq")]),
        )
        # entry_type: 0 = bid order, 1 = ask order, 2 = trade
        .filter(
            (pl.col("sec_id") == sid)
            & pl.col("entry_type").is_in(["0", "1", "2"])
            & pl.col("action").is_in([A_NEW, A_CHANGE, A_DELETE])
            & pl.col("mantissa").is_not_null()
        )
        .with_columns(
            side=pl.col("entry_type").replace_strict({"0": BID, "1": ASK, "2": TRADE}, return_dtype=pl.Int8),
            price=(pl.col("mantissa") * (pl.lit(10.0) ** pl.col("exponent"))),
            # order_id is a numeric string; int64 keeps the replay map cheap
            oid=pl.col("order_id").cast(pl.Int64, strict=False).fill_null(-1),
        )
        .select(
            pl.col("rpt").cast(pl.Int64),
            pl.col("sending_time").cast(pl.Int64),
            pl.col("side"),
            pl.col("action").cast(pl.Int8),
            pl.col("oid"),
            pl.col("price").cast(pl.Float64),
            pl.col("size").cast(pl.Int64).fill_null(0),
        )
        .sort("rpt")
    )

    lf.sink_parquet(out, compression="zstd")
    return out


# ==============================================================================
# PART 2: BOOK REPLAY + CLASSIFICATION
# ==============================================================================
def _nivel3(cnt, sz, best, passo, n_ticks, limite=400):
    """Walk up to 3 occupied price levels outward from `best`.

    Returns (total_size, size_weighted_tick_sum, levels_found). The scan is
    bounded by `limite` ticks so a sparse book cannot make this O(n).
    """
    q_tot = 0
    pq_tot = 0.0
    achados = 0
    j = best
    fim = 0
    while achados < 3 and 0 <= j < n_ticks and fim < limite:
        if cnt[j] > 0:
            q = sz[j]
            if q > 0:
                q_tot += q
                pq_tot += j * q
                achados += 1
        j += passo
        fim += 1
    return q_tot, pq_tot, achados


def _balde(d: int) -> int:
    """Bucket a tick-distance from the touch into the depth histogram."""
    if d <= 5:
        return d                       # 0..5 get their own bucket
    if d <= 10:
        return 6
    if d <= 20:
        return 7
    if d <= 50:
        return 8
    if d <= 100:
        return 9
    return 10


def replay_dia(events_path: Path, bucket: str = "1m",
               rec_ini_ms: int | None = None, rec_fim_ms: int | None = None,
               grid_ms: int | None = None) -> dict | None:
    """Replay one day's order book and classify every new order.

    `bucket` sets the aggregation grain: "1m" (default) or "1s". The book replay
    itself is identical either way -- only the accumulator grid changes -- so a
    second-resolution run is exactly the same measurement, just binned finer.

    If `rec_ini_ms`/`rec_fim_ms` are given (ms since midnight UTC), every book
    event inside that window is also recorded individually -- best bid/ask after
    the event plus, for new orders, the price and what the order did to the
    spread. That is what the event-sequence chart draws.

    If `grid_ms` is set, additionally builds a CONTINUOUS price grid at that
    resolution (microprice and L3 depth-weighted midprice) for the regular
    session, and records every strict undercut with the price immediately
    before and immediately after it. This supports event-study measurement of
    the price response, which uses ~1.5M events instead of a few thousand
    window aggregates.

    Returns {"minutes": polars.DataFrame, "report": dict, "events": list}.
    """
    if bucket not in ("1m", "1s"):
        raise ValueError(f"bucket must be '1m' or '1s', got {bucket!r}")
    # sending_time is YYYYMMDDHHMMSSmmm
    div = 100_000 if bucket == "1m" else 1_000
    fmt = "%Y%m%d%H%M" if bucket == "1m" else "%Y%m%d%H%M%S"
    df = pl.read_parquet(events_path).sort("rpt")
    if df.height == 0:
        return None

    date_str = events_path.stem.split("_")[-1]

    side = df["side"].to_numpy()
    action = df["action"].to_numpy()
    oid = df["oid"].to_numpy()
    price = df["price"].to_numpy()
    size = df["size"].to_numpy()
    stime = df["sending_time"].to_numpy()

    # ---- price -> tick index -------------------------------------------------
    p_min, p_max = float(price.min()), float(price.max())
    base = p_min - 10 * TICK_SIZE
    ticks = np.rint((price - base) / TICK_SIZE).astype(np.int64)
    n_ticks = int(np.rint((p_max - base) / TICK_SIZE)) + 11

    # ---- minute bucket: sending_time is YYYYMMDDHHMMSSmmm --------------------
    mkey = stime // div                              # -> YYYYMMDDHHMM[SS]
    minutes, m_idx = np.unique(mkey, return_inverse=True)

    # wall-clock position of every event, in ms since midnight, so spread states
    # can be weighted by how long they actually held
    tod = stime % 1_000_000_000                      # -> HHMMSSmmm
    ms_day = ((tod // 10_000_000) * 3_600_000
              + ((tod // 100_000) % 100) * 60_000
              + ((tod // 1_000) % 100) * 1_000
              + (tod % 1_000))
    acc = np.zeros((minutes.size, NCOL), dtype=np.int64)
    last_mid_tick = np.full(minutes.size, np.nan)
    last_trade_tick = np.full(minutes.size, np.nan)

    # ---- book state ---------------------------------------------------------
    cnt_bid = np.zeros(n_ticks, dtype=np.int32)
    cnt_ask = np.zeros(n_ticks, dtype=np.int32)
    sz_bid = np.zeros(n_ticks, dtype=np.int64)       # resting contracts per tick
    sz_ask = np.zeros(n_ticks, dtype=np.int64)
    order_tick: dict[int, int] = {}                  # live order_id -> tick
    order_size: dict[int, int] = {}                  # live order_id -> size
    best_bid = -1                                    # -1 = side empty
    best_ask = n_ticks                               # n_ticks = side empty
    n_bid = n_ask = 0

    # naive heuristic state (mirrors undercutting_analise.py exactly)
    naive_bid = naive_ask = np.nan

    # ---- continuous price grid + undercut event study ----------------------
    SESSAO_INI_MS = 12 * 3_600_000      # 09:00 BRT in UTC ms
    SESSAO_FIM_MS = 21 * 3_600_000      # 18:00 BRT in UTC ms
    if grid_ms:
        n_grid = (SESSAO_FIM_MS - SESSAO_INI_MS) // grid_ms + 2
        grid_mp = np.full(n_grid, np.nan)
        grid_l3 = np.full(n_grid, np.nan)
        grid_mid = np.full(n_grid, np.nan)
        ev_i, ev_lado, ev_qtd, ev_tipo = [], [], [], []
        ev_sp_pre, ev_melhora = [], []
        ev_mp_pre, ev_mp_pos = [], []
        ev_l3_pre, ev_l3_pos = [], []
        ev_mid_pre, ev_mid_pos = [], []
        g_ultimo = -1
        _mp0 = _l30 = _mid0 = np.nan
    else:
        grid_mp = grid_l3 = grid_mid = None
        ev_i = ev_lado = ev_qtd = ev_tipo = None
        ev_sp_pre = ev_melhora = None
        ev_mp_pre = ev_mp_pos = ev_l3_pre = ev_l3_pos = None
        ev_mid_pre = ev_mid_pos = None

    def _precos():
        """(microprice, L3 midprice, plain midpoint) in TICKS, or nans.

        The plain midpoint is the control: it changes only when a QUOTE changes,
        so it cannot drift from queue building the way a size-weighted price can.
        """
        if not (n_bid and n_ask) or best_ask <= best_bid:
            return (np.nan, np.nan, np.nan)
        mid = (best_bid + best_ask) / 2.0
        szb = sz_bid[best_bid]
        sza = sz_ask[best_ask]
        if szb <= 0 or sza <= 0:
            return (np.nan, np.nan, mid)
        mp = (szb * best_ask + sza * best_bid) / (szb + sza)
        qb, pqb, nb_ = _nivel3(cnt_bid, sz_bid, best_bid, -1, n_ticks)
        qa, pqa, na_ = _nivel3(cnt_ask, sz_ask, best_ask, +1, n_ticks)
        if qb <= 0 or qa <= 0:
            return (mp, np.nan, mid)
        vb = pqb / qb          # size-weighted bid price over <=3 levels
        va = pqa / qa          # size-weighted ask price over <=3 levels
        l3 = (qb * va + qa * vb) / (qb + qa)
        return (mp, l3, mid)

    # event recorder (only inside the requested window)
    rec: list[tuple] = []
    recording = rec_ini_ms is not None
    lbl = 0          # 0 none, 1 STRICT undercut, 2 join best, 3 behind,
                     # 4 aggressive, 5 no ref, 6 ambiguous undercut (trade-adjacent)

    # integrity counters
    orphan_delete = 0
    orphan_change = 0
    locked_book_events = 0

    # local aliases (hot loop)
    c_new_bid, c_new_ask = C["new_bid"], C["new_ask"]
    c_uc_b, c_uc_s = C["undercut_buy_count"], C["undercut_sell_count"]
    c_jb_b, c_jb_s = C["join_best_buy"], C["join_best_sell"]
    c_bh_b, c_bh_s = C["behind_best_buy"], C["behind_best_sell"]
    c_ag_b, c_ag_s = C["aggressive_buy"], C["aggressive_sell"]
    c_nr_b, c_nr_s = C["no_reference_buy"], C["no_reference_sell"]
    c_narrow, c_same, c_wide = C["spread_narrowed"], C["spread_unchanged"], C["spread_widened_by_new"]
    c_uc_ticks = C["undercut_ticks_sum"]
    c_resid = C["undercut_maybe_residual"]
    c_res_b, c_res_s = C["undercut_resid_buy"], C["undercut_resid_sell"]
    c_msg = C["msg_total"]
    c_mid_sum, c_mid_sq, c_mid_obs = C["mid_sum"], C["mid_sq"], C["mid_obs"]
    c_sp_all, c_sp_all_obs = C["spread_all_sum"], C["spread_all_obs"]
    c_ucv_b, c_ucv_s = C["uc_strict_vol_bid"], C["uc_strict_vol_ask"]
    c_amv_b, c_amv_s = C["uc_amb_vol_bid"], C["uc_amb_vol_ask"]
    c_nv_vb, c_nv_vs = C["new_vol_bid"], C["new_vol_ask"]
    c_mp_obs = C["mp_obs"]
    c_tc_nb, c_tc_na = C["touch_cancel_n_bid"], C["touch_cancel_n_ask"]
    c_tl_b, c_tl_a = C["touch_life_bid"], C["touch_life_ask"]
    c_td_b, c_td_a, c_td_obs = C["touch_depth_bid"], C["touch_depth_ask"], C["touch_depth_obs"]
    c_twd_b, c_twd_a = C["touch_wd_vol_bid"], C["touch_wd_vol_ask"]
    c_dwd_b, c_dwd_a = C["deep_wd_vol_bid"], C["deep_wd_vol_ask"]
    c_fq_b, c_fq_a = C["fq_wd_bid"], C["fq_wd_ask"]
    c_bq_b, c_bq_a = C["bq_wd_bid"], C["bq_wd_ask"]
    c_qps_b, c_qps_a = C["qp_sum_bid"], C["qp_sum_ask"]
    c_qpn_b, c_qpn_a = C["qp_n_bid"], C["qp_n_ask"]
    c_urn_b, c_urn_a = C["ur_run_n_bid"], C["ur_run_n_ask"]
    c_url_b, c_url_a = C["ur_run_len_bid"], C["ur_run_len_ask"]
    c_qcn_b, c_qcn_a = C["qcr_n_bid"], C["qcr_n_ask"]
    c_qcl_b, c_qcl_a = C["qcr_len_bid"], C["qcr_len_ask"]
    # FIFO queue per price level: tick -> {oid: remaining size}. Python dicts
    # preserve insertion order, which IS the exchange's time priority.
    fila_bid: dict[int, dict] = {}
    fila_ask: dict[int, dict] = {}
    QP_FRENTE, QP_FUNDO = 0.20, 0.80
    TAU_MAX_MS = 1000                 # SPEC 2.4 tau_max
    ur_lado, ur_len = -1, 0           # price undercutting run state
    qcr_lado, qcr_len, qcr_ms = -1, 0, -1   # queue cancellation run state
    c_tad_b, c_tad_a = C["touch_add_vol_bid"], C["touch_add_vol_ask"]
    order_time: dict[int, int] = {}      # oid -> ms_day at insertion
    # close-of-window prices (last write wins) -- PROTOCOL 3.5
    last_mp_tick = np.full(minutes.size, np.nan)
    last_mid_all = np.full(minutes.size, np.nan)
    c_dep = (C["depth_0"], C["depth_1"], C["depth_2"], C["depth_3"], C["depth_4"],
             C["depth_5"], C["depth_6_10"], C["depth_11_20"], C["depth_21_50"],
             C["depth_51_100"], C["depth_100p"])
    c_dep_sum, c_dep_obs = C["depth_ticks_sum"], C["depth_obs"]
    c_sw_b, c_sw_s = C["sweep_buy"], C["sweep_sell"]
    sweep_ms, sweep_agr, sweep_prints, sweep_prints_max = -1, -2, 0, 0
    depth_max = 0
    c_rp_tot = C["reprice_total"]
    c_rp_nb, c_rp_ns = C["reprice_narrow_buy"], C["reprice_narrow_sell"]
    c_rp_wide, c_rp_neu = C["reprice_widen"], C["reprice_neutral"]
    c_rp_size, c_rp_ticks = C["reprice_size_only"], C["reprice_ticks_sum"]
    JANELA_RESIDUO = 3          # events after a trade still considered adjacent
    desde_trade = 10**9         # events since the last trade
    agr_recente = -1            # aggressor side of that trade: 0 buy, 1 sell
    c_del_wide = C["deletes_widening_spread"]
    c_locked = C["new_orders_in_locked_book"]
    c_tcnt, c_tvol = C["trade_count"], C["trade_volume"]
    c_spsum, c_spobs = C["spread_ticks_sum"], C["spread_obs"]
    c_bbsz, c_basz = C["best_bid_size_sum"], C["best_ask_size_sum"]
    c_ms = (C["spread_ms_1"], C["spread_ms_2"], C["spread_ms_3"], C["spread_ms_4p"])
    c_n = (C["spread_n_1"], C["spread_n_2"], C["spread_n_3"], C["spread_n_4p"])
    c_ms_drop = C["spread_ms_dropped"]
    MAX_DT_MS = 2_000          # ignore auction/halt gaps, which are not a spread state
    prev_ms = -1
    prev_m = 0
    prev_spread = -1
    c_nv_b, c_nv_s = C["naive_undercut_buy"], C["naive_undercut_sell"]

    for i in range(side.size):
        s = side[i]
        m = m_idx[i]
        row = acc[m]
        row[c_msg] += 1
        if grid_ms is not None:
            _t = ms_day[i]
            if SESSAO_INI_MS <= _t <= SESSAO_FIM_MS:
                gi = (_t - SESSAO_INI_MS) // grid_ms
                if gi != g_ultimo:
                    _m, _l, _d = _precos()
                    grid_mp[gi] = _m
                    grid_l3[gi] = _l
                    grid_mid[gi] = _d
                    g_ultimo = gi
        # thesis samples the prevailing book on every message row
        if n_bid and n_ask and best_ask > best_bid:
            soma = best_bid + best_ask
            row[c_mid_sum] += soma
            row[c_mid_sq] += soma * soma
            row[c_mid_obs] += 1
            row[c_sp_all] += best_ask - best_bid
            row[c_sp_all_obs] += 1
            last_mid_all[m] = soma / 2.0
            _szb = sz_bid[best_bid]
            _sza = sz_ask[best_ask]
            row[c_td_b] += _szb
            row[c_td_a] += _sza
            row[c_td_obs] += 1
            if _szb > 0 and _sza > 0:
                # microprice: size-weighted toward the side with less depth
                last_mp_tick[m] = (_szb * best_ask + _sza * best_bid) / (_szb + _sza)
                row[c_mp_obs] += 1

        # time-weighting: the interval since the previous event was spent at
        # `prev_spread`, so credit it to that bucket in the minute it began
        if prev_ms >= 0 and prev_spread > 0:
            dt = ms_day[i] - prev_ms
            if 0 < dt <= MAX_DT_MS:
                acc[prev_m, c_ms[min(prev_spread, 4) - 1]] += dt
            elif dt > MAX_DT_MS:
                acc[prev_m, c_ms_drop] += dt
        prev_ms = ms_day[i]
        prev_m = m

        # ---------------- trades ------------------------------------------
        if s == TRADE:
            row[c_tcnt] += 1
            row[c_tvol] += size[i]
            last_trade_tick[m] = ticks[i]
            if n_bid and n_ask:
                agr_recente = (BID if ticks[i] >= best_ask
                               else (ASK if ticks[i] <= best_bid else -1))
            else:
                agr_recente = -1
            desde_trade = 0
            # a new incoming order starts whenever the timestamp or the
            # aggressor side changes; otherwise this print continues the
            # same order eating deeper into the book
            if ms_day[i] != sweep_ms or agr_recente != sweep_agr:
                if sweep_prints > sweep_prints_max:
                    sweep_prints_max = sweep_prints
                sweep_prints = 1
                sweep_ms, sweep_agr = ms_day[i], agr_recente
                if agr_recente == BID:
                    row[c_sw_b] += 1
                elif agr_recente == ASK:
                    row[c_sw_s] += 1
            else:
                sweep_prints += 1
            if qcr_len > 0:
                row[c_qcn_a if qcr_lado == ASK else c_qcn_b] += 1
                row[c_qcl_a if qcr_lado == ASK else c_qcl_b] += qcr_len
            qcr_lado, qcr_len = -1, 0
            if recording and rec_ini_ms <= ms_day[i] <= rec_fim_ms:
                # action 9 = trade; bb/ba are the book as it stands at the match
                rec.append((int(ms_day[i]), 9, 2, int(ticks[i]),
                            int(best_bid), int(best_ask), 0, int(size[i])))
            continue

        a = action[i]
        t = ticks[i]

        # ---------------- naive heuristic (for comparison only) -----------
        # The old script forward-fills any bid/ask price on New/Change and
        # compares the incoming price against the one-row-lagged values.
        prev_nb, prev_na = naive_bid, naive_ask
        if a in (A_NEW, A_CHANGE):
            if s == BID:
                naive_bid = price[i]
            else:
                naive_ask = price[i]
        if a == A_NEW and prev_nb == prev_nb and prev_na == prev_na:  # both non-NaN
            if s == BID and prev_nb < price[i] < prev_na:
                row[c_nv_b] += 1
            elif s == ASK and prev_nb < price[i] < prev_na:
                row[c_nv_s] += 1

        # ---------------- real book replay --------------------------------
        spread_before = best_ask - best_bid if (n_bid and n_ask) else -1

        if a == A_NEW:
            if s == BID:
                row[c_new_bid] += 1
                row[c_nv_vb] += size[i]
                # classify BEFORE mutating the book
                if not n_bid or not n_ask:
                    row[c_nr_b] += 1
                    lbl = 5
                elif t >= best_ask:
                    row[c_ag_b] += 1
                    locked_book_events += 1
                    lbl = 4
                elif t > best_bid:
                    row[c_uc_b] += 1
                    row[c_uc_ticks] += t - best_bid
                    if desde_trade < JANELA_RESIDUO and agr_recente == BID:
                        row[c_resid] += 1
                        row[c_res_b] += 1
                        row[c_amv_b] += size[i]
                        lbl = 6          # ambiguous: may be an aggressor residual
                    else:
                        row[c_ucv_b] += size[i]
                        lbl = 1          # strict undercut
                    if ur_lado == BID:
                        ur_len += 1
                    else:
                        if ur_len > 0:
                            row[c_urn_a if ur_lado == ASK else c_urn_b] += 1
                            row[c_url_a if ur_lado == ASK else c_url_b] += ur_len
                        ur_lado, ur_len = BID, 1
                elif t == best_bid:
                    row[c_jb_b] += 1
                    lbl = 2
                else:
                    row[c_bh_b] += 1
                    lbl = 3
                if lbl == 2 or lbl == 3:        # at the touch, or behind it
                    d = best_bid - t
                    row[c_dep[_balde(d)]] += 1
                    row[c_dep_sum] += d
                    row[c_dep_obs] += 1
                    if d > depth_max:
                        depth_max = d
                if grid_ms is not None and (lbl == 1 or lbl == 2):
                    _mp0, _l30, _mid0 = _precos()
                    _sp0 = best_ask - best_bid if (n_bid and n_ask) else -1
                    _melh = t - best_bid if n_bid else 0
                cnt_bid[t] += 1
                sz_bid[t] += size[i]
                fila_bid.setdefault(t, {})[oid[i]] = size[i]
                n_bid += 1
                if t > best_bid:
                    best_bid = t
            else:
                row[c_new_ask] += 1
                row[c_nv_vs] += size[i]
                if not n_bid or not n_ask:
                    row[c_nr_s] += 1
                    lbl = 5
                elif t <= best_bid:
                    row[c_ag_s] += 1
                    locked_book_events += 1
                    lbl = 4
                elif t < best_ask:
                    row[c_uc_s] += 1
                    row[c_uc_ticks] += best_ask - t
                    if desde_trade < JANELA_RESIDUO and agr_recente == ASK:
                        row[c_resid] += 1
                        row[c_res_s] += 1
                        row[c_amv_s] += size[i]
                        lbl = 6
                    else:
                        row[c_ucv_s] += size[i]
                        lbl = 1
                    if ur_lado == ASK:
                        ur_len += 1
                    else:
                        if ur_len > 0:
                            row[c_urn_b if ur_lado == BID else c_urn_a] += 1
                            row[c_url_b if ur_lado == BID else c_url_a] += ur_len
                        ur_lado, ur_len = ASK, 1
                elif t == best_ask:
                    row[c_jb_s] += 1
                    lbl = 2
                else:
                    row[c_bh_s] += 1
                    lbl = 3
                if lbl == 2 or lbl == 3:
                    d = t - best_ask
                    row[c_dep[_balde(d)]] += 1
                    row[c_dep_sum] += d
                    row[c_dep_obs] += 1
                    if d > depth_max:
                        depth_max = d
                if grid_ms is not None and (lbl == 1 or lbl == 2):
                    _mp0, _l30, _mid0 = _precos()
                    _sp0 = best_ask - best_bid if (n_bid and n_ask) else -1
                    _melh = best_ask - t if n_ask else 0
                cnt_ask[t] += 1
                sz_ask[t] += size[i]
                fila_ask.setdefault(t, {})[oid[i]] = size[i]
                n_ask += 1
                if t < best_ask:
                    best_ask = t
            order_tick[oid[i]] = t
            order_size[oid[i]] = size[i]
            order_time[oid[i]] = ms_day[i]
            # volume added AT the touch (join or undercut, i.e. it became/was best)
            if s == BID:
                if t == best_bid:
                    row[c_tad_b] += size[i]
            elif t == best_ask:
                row[c_tad_a] += size[i]

            # ---- event study: strict undercut, pre vs immediately after ----
            if (grid_ms is not None and (lbl == 1 or lbl == 2)
                    and SESSAO_INI_MS <= ms_day[i] <= SESSAO_FIM_MS):
                _mp1, _l31, _mid1 = _precos()
                ev_i.append(int((ms_day[i] - SESSAO_INI_MS) // grid_ms))
                ev_lado.append(int(s))
                ev_qtd.append(int(size[i]))
                ev_tipo.append(int(lbl))          # 1 = undercut, 2 = join at best
                ev_mp_pre.append(_mp0)
                ev_mp_pos.append(_mp1)
                ev_l3_pre.append(_l30)
                ev_l3_pos.append(_l31)
                ev_mid_pre.append(_mid0)
                ev_mid_pos.append(_mid1)
                ev_sp_pre.append(int(_sp0))
                ev_melhora.append(int(_melh))

            # ---- ground truth: did the spread actually move? -------------
            spread_after = best_ask - best_bid if (n_bid and n_ask) else -1
            if spread_before > 0 and spread_after > 0:
                if spread_after < spread_before:
                    row[c_narrow] += 1
                elif spread_after == spread_before:
                    row[c_same] += 1
                else:
                    row[c_wide] += 1
                row[c_spsum] += spread_after
                row[c_spobs] += 1
                row[c_bbsz] += sz_bid[best_bid]
                row[c_basz] += sz_ask[best_ask]
                row[c_n[min(spread_after, 4) - 1]] += 1
                last_mid_tick[m] = (best_bid + best_ask) / 2.0
            elif spread_before >= 0 or spread_after >= 0:
                # book locked (spread 0) or crossed (spread < 0) on either side of
                # the event -- no meaningful spread delta to measure
                row[c_locked] += 1

        elif a == A_DELETE:
            old = order_tick.pop(oid[i], -1)
            osz = order_size.pop(oid[i], 0)
            _t0 = order_time.pop(oid[i], -1)
            if old >= 0 and _t0 >= 0:
                # was this order sitting at the top of book when it died?
                if s == BID and old == best_bid:
                    row[c_tc_nb] += 1
                    row[c_tl_b] += ms_day[i] - _t0
                    row[c_twd_b] += osz
                    # SPEC 2.2 — QP = volume ahead / total depth at level 1.
                    # The dict is in insertion order, which is time priority.
                    _tot = sz_bid[old]
                    if _tot > 0:
                        _ah = 0
                        for _o, _q in fila_bid.get(old, {}).items():
                            if _o == oid[i]:
                                break
                            _ah += _q
                        _qp = _ah / _tot
                        row[c_qps_b] += int(round(_qp * 10000))
                        row[c_qpn_b] += 1
                        if _qp <= QP_FRENTE:
                            row[c_fq_b] += osz
                            # SPEC 2.4 — front-of-queue cancellation run
                            if (qcr_lado == BID and qcr_ms >= 0
                                    and ms_day[i] - qcr_ms <= TAU_MAX_MS):
                                qcr_len += 1
                            else:
                                if qcr_len > 0:
                                    row[c_qcn_a if qcr_lado == ASK else c_qcn_b] += 1
                                    row[c_qcl_a if qcr_lado == ASK else c_qcl_b] += qcr_len
                                qcr_lado, qcr_len = BID, 1
                            qcr_ms = ms_day[i]
                        elif _qp > QP_FUNDO:
                            row[c_bq_b] += osz
                elif s == ASK and old == best_ask:
                    row[c_tc_na] += 1
                    row[c_tl_a] += ms_day[i] - _t0
                    row[c_twd_a] += osz
                    _tot = sz_ask[old]
                    if _tot > 0:
                        _ah = 0
                        for _o, _q in fila_ask.get(old, {}).items():
                            if _o == oid[i]:
                                break
                            _ah += _q
                        _qp = _ah / _tot
                        row[c_qps_a] += int(round(_qp * 10000))
                        row[c_qpn_a] += 1
                        if _qp <= QP_FRENTE:
                            row[c_fq_a] += osz
                            if (qcr_lado == ASK and qcr_ms >= 0
                                    and ms_day[i] - qcr_ms <= TAU_MAX_MS):
                                qcr_len += 1
                            else:
                                if qcr_len > 0:
                                    row[c_qcn_b if qcr_lado == BID else c_qcn_a] += 1
                                    row[c_qcl_b if qcr_lado == BID else c_qcl_a] += qcr_len
                                qcr_lado, qcr_len = ASK, 1
                            qcr_ms = ms_day[i]
                        elif _qp > QP_FUNDO:
                            row[c_bq_a] += osz
                elif s == BID:
                    row[c_dwd_b] += osz          # cancelled away from the touch
                else:
                    row[c_dwd_a] += osz
            if old < 0:
                old = t                              # fall back to message price
                osz = size[i]
                orphan_delete += 1
            if s == BID:
                _f = fila_bid.get(old)
                if _f is not None:
                    _f.pop(oid[i], None)
                    if not _f:
                        del fila_bid[old]
                if cnt_bid[old] > 0:
                    cnt_bid[old] -= 1
                    sz_bid[old] -= osz
                    n_bid -= 1
                    if old == best_bid and cnt_bid[old] == 0:
                        j = old - 1
                        while j >= 0 and cnt_bid[j] == 0:
                            j -= 1
                        best_bid = j
            else:
                _f = fila_ask.get(old)
                if _f is not None:
                    _f.pop(oid[i], None)
                    if not _f:
                        del fila_ask[old]
                if cnt_ask[old] > 0:
                    cnt_ask[old] -= 1
                    sz_ask[old] -= osz
                    n_ask -= 1
                    if old == best_ask and cnt_ask[old] == 0:
                        j = old + 1
                        while j < n_ticks and cnt_ask[j] == 0:
                            j += 1
                        best_ask = j
            spread_after = best_ask - best_bid if (n_bid and n_ask) else -1
            if spread_before >= 0 and spread_after > spread_before:
                row[c_del_wide] += 1

        else:  # A_CHANGE -> move and/or resize
            old = order_tick.get(oid[i], -1)
            osz = order_size.get(oid[i], 0)
            row[c_rp_tot] += 1
            mesmo_preco = (old == t)
            conhecido = old >= 0
            if old < 0:
                # never seen this order: treat as an insert so the arrays stay
                # consistent with the order maps
                orphan_change += 1
                old, osz = t, 0
                if s == BID:
                    cnt_bid[t] += 1
                    n_bid += 1
                    if t > best_bid:
                        best_bid = t
                else:
                    cnt_ask[t] += 1
                    n_ask += 1
                    if t < best_ask:
                        best_ask = t
            elif old != t:
                if s == BID:
                    if cnt_bid[old] > 0:
                        cnt_bid[old] -= 1
                        n_bid -= 1
                        if old == best_bid and cnt_bid[old] == 0:
                            j = old - 1
                            while j >= 0 and cnt_bid[j] == 0:
                                j -= 1
                            best_bid = j
                    cnt_bid[t] += 1
                    n_bid += 1
                    if t > best_bid:
                        best_bid = t
                else:
                    if cnt_ask[old] > 0:
                        cnt_ask[old] -= 1
                        n_ask -= 1
                        if old == best_ask and cnt_ask[old] == 0:
                            j = old + 1
                            while j < n_ticks and cnt_ask[j] == 0:
                                j += 1
                            best_ask = j
                    cnt_ask[t] += 1
                    n_ask += 1
                    if t < best_ask:
                        best_ask = t
            # size moves whether or not the price changed (a pure resize keeps
            # old == t, and must still be reflected at the touch).
            # A partial fill KEEPS queue priority, so updating the value of an
            # existing dict key is correct -- insertion order is preserved.
            if s == BID:
                sz_bid[old] -= osz
                sz_bid[t] += size[i]
                if old != t:
                    _f = fila_bid.get(old)
                    if _f is not None:
                        _f.pop(oid[i], None)
                        if not _f:
                            del fila_bid[old]
                    fila_bid.setdefault(t, {})[oid[i]] = size[i]
                else:
                    fila_bid.setdefault(t, {})[oid[i]] = size[i]
            else:
                sz_ask[old] -= osz
                sz_ask[t] += size[i]
                if old != t:
                    _f = fila_ask.get(old)
                    if _f is not None:
                        _f.pop(oid[i], None)
                        if not _f:
                            del fila_ask[old]
                    fila_ask.setdefault(t, {})[oid[i]] = size[i]
                else:
                    fila_ask.setdefault(t, {})[oid[i]] = size[i]
            order_tick[oid[i]] = t
            order_size[oid[i]] = size[i]
            if oid[i] not in order_time:
                order_time[oid[i]] = ms_day[i]

            # classify the reprice by what it actually did to the spread
            spread_after = best_ask - best_bid if (n_bid and n_ask) else -1
            if not conhecido:
                pass                                   # orphan: nothing to compare
            elif mesmo_preco:
                row[c_rp_size] += 1                    # pure resize, price unmoved
            elif spread_before > 0 and spread_after > 0:
                if spread_after < spread_before:
                    row[c_rp_nb if s == BID else c_rp_ns] += 1
                    row[c_rp_ticks] += spread_before - spread_after
                elif spread_after > spread_before:
                    row[c_rp_wide] += 1
                else:
                    row[c_rp_neu] += 1

        desde_trade += 1

        # spread now standing -- this is what the next interval will be spent at
        prev_spread = best_ask - best_bid if (n_bid and n_ask) else -1

        # refresh close-of-window prices with the POST-event book, so the last
        # value in a window is the state the window actually closed at
        if n_bid and n_ask and best_ask > best_bid:
            last_mid_all[m] = (best_bid + best_ask) / 2.0
            _szb = sz_bid[best_bid]
            _sza = sz_ask[best_ask]
            if _szb > 0 and _sza > 0:
                last_mp_tick[m] = (_szb * best_ask + _sza * best_bid) / (_szb + _sza)

        if recording and rec_ini_ms <= ms_day[i] <= rec_fim_ms:
            rec.append((int(ms_day[i]), int(a), int(s), int(t),
                        int(best_bid), int(best_ask), lbl, int(size[i])))
        lbl = 0

    # ---- assemble per-minute panel -----------------------------------------
    out = pl.DataFrame({"window_key": minutes})
    for name, j in C.items():
        out = out.with_columns(pl.Series(name, acc[:, j]))
    # price-space sums (exact), so aggregating 1-min windows to 5-min is a sum.
    # m_i = (bb+ba)/2 in ticks; price_i = base + m_i*TICK
    _obs = acc[:, C["mid_obs"]].astype(np.float64)
    _sum = acc[:, C["mid_sum"]].astype(np.float64)
    _sq = acc[:, C["mid_sq"]].astype(np.float64)
    out = out.with_columns(
        pl.Series("mid_price_sum", base * _obs + (_sum / 2.0) * TICK_SIZE),
        pl.Series("mid_price_sq_sum",
                  _obs * base**2 + base * TICK_SIZE * _sum
                  + (TICK_SIZE**2) * _sq / 4.0),
        pl.Series("spread_pts_sum",
                  acc[:, C["spread_all_sum"]].astype(np.float64) * TICK_SIZE),
    )
    out = out.with_columns(
        pl.Series("close_microprice", base + last_mp_tick * TICK_SIZE),
        pl.Series("close_midprice", base + last_mid_all * TICK_SIZE),
    )
    out = out.with_columns(
        pl.Series("mid_price", base + last_mid_tick * TICK_SIZE),
        pl.Series("last_trade_price", base + last_trade_tick * TICK_SIZE),
        avg_spread_ticks=pl.when(pl.col("spread_obs") > 0)
        .then(pl.col("spread_ticks_sum") / pl.col("spread_obs"))
        .otherwise(None),
        date=pl.lit(date_str),
    ).with_columns(
        window_1m=pl.col("window_key").cast(pl.String).str.to_datetime(fmt, strict=False)
    )

    tot = {k: int(acc[:, j].sum()) for k, j in C.items()}
    new_total = tot["new_bid"] + tot["new_ask"]
    real_uc = tot["undercut_buy_count"] + tot["undercut_sell_count"]
    unchanged = tot["join_best_buy"] + tot["join_best_sell"] + tot["behind_best_buy"] + tot["behind_best_sell"]
    report = {
        "date": date_str,
        "events_total": int(side.size),
        "new_orders_total": new_total,
        "undercut_real": real_uc,
        "undercut_pct_of_new": round(100.0 * real_uc / new_total, 4) if new_total else 0.0,
        "undercut_buy": tot["undercut_buy_count"],
        "undercut_sell": tot["undercut_sell_count"],
        "undercut_ticks_sum": tot["undercut_ticks_sum"],
        "undercut_avg_ticks": round(tot["undercut_ticks_sum"] / real_uc, 4) if real_uc else 0.0,
        "undercut_maybe_residual": tot["undercut_maybe_residual"],
        "undercut_resid_buy": tot["undercut_resid_buy"],
        "undercut_resid_sell": tot["undercut_resid_sell"],
        "undercut_strict_buy": tot["undercut_buy_count"] - tot["undercut_resid_buy"],
        "undercut_strict_sell": tot["undercut_sell_count"] - tot["undercut_resid_sell"],
        "msg_total": tot["msg_total"],
        "sweep_buy": tot["sweep_buy"],
        "sweep_sell": tot["sweep_sell"],
        "sweep_total": tot["sweep_buy"] + tot["sweep_sell"],
        "prints_per_sweep": round(
            tot["trade_count"] / (tot["sweep_buy"] + tot["sweep_sell"]), 4)
            if (tot["sweep_buy"] + tot["sweep_sell"]) else 0.0,
        "sweep_prints_max": max(sweep_prints_max, sweep_prints),
        "depth_obs": tot["depth_obs"],
        "depth_mean_ticks": round(tot["depth_ticks_sum"] / tot["depth_obs"], 4) if tot["depth_obs"] else 0.0,
        "depth_max_ticks": depth_max,
        **{k: tot[k] for k in ("depth_0", "depth_1", "depth_2", "depth_3", "depth_4",
                               "depth_5", "depth_6_10", "depth_11_20", "depth_21_50",
                               "depth_51_100", "depth_100p")},
        "reprice_total": tot["reprice_total"],
        "reprice_narrow": tot["reprice_narrow_buy"] + tot["reprice_narrow_sell"],
        "reprice_narrow_buy": tot["reprice_narrow_buy"],
        "reprice_narrow_sell": tot["reprice_narrow_sell"],
        "reprice_widen": tot["reprice_widen"],
        "reprice_neutral": tot["reprice_neutral"],
        "reprice_size_only": tot["reprice_size_only"],
        "undercut_incl_reprice": real_uc + tot["reprice_narrow_buy"] + tot["reprice_narrow_sell"],
        "undercut_estrito": real_uc - tot["undercut_maybe_residual"],
        "residual_pct_of_undercut": round(
            100.0 * tot["undercut_maybe_residual"] / real_uc, 4) if real_uc else 0.0,
        "no_spread_change_total": unchanged,
        "no_spread_change_pct_of_new": round(100.0 * unchanged / new_total, 4) if new_total else 0.0,
        "join_best_buy": tot["join_best_buy"],
        "join_best_sell": tot["join_best_sell"],
        "behind_best_buy": tot["behind_best_buy"],
        "behind_best_sell": tot["behind_best_sell"],
        "aggressive_buy": tot["aggressive_buy"],
        "aggressive_sell": tot["aggressive_sell"],
        "no_reference": tot["no_reference_buy"] + tot["no_reference_sell"],
        # ground truth measured on the book itself
        "measured_narrowed": tot["spread_narrowed"],
        "measured_unchanged": tot["spread_unchanged"],
        "measured_widened": tot["spread_widened_by_new"],
        "narrowed_vs_undercut_delta": tot["spread_narrowed"] - real_uc,
        "new_orders_in_locked_book": tot["new_orders_in_locked_book"],
        "deletes_widening_spread": tot["deletes_widening_spread"],
        "trade_count": tot["trade_count"],
        "trade_volume": tot["trade_volume"],
        # old heuristic, same input stream
        "naive_undercut_total": tot["naive_undercut_buy"] + tot["naive_undercut_sell"],
        "naive_over_count_factor": round(
            (tot["naive_undercut_buy"] + tot["naive_undercut_sell"]) / real_uc, 4
        ) if real_uc else 0.0,
        # integrity
        "orphan_deletes": orphan_delete,
        "orphan_changes": orphan_change,
        "locked_or_crossed_new": locked_book_events,
        "final_book_orders": n_bid + n_ask,
        "neg_size_ticks": int((sz_bid < 0).sum() + (sz_ask < 0).sum()),
    }
    saida = {"minutes": out, "report": report, "events": rec,
             "tick_base": base, "tick_size": TICK_SIZE}
    if grid_ms is not None:
        saida["grid"] = {
            "grid_ms": grid_ms,
            "ini_ms": SESSAO_INI_MS,
            "mp": base + grid_mp * TICK_SIZE,
            "l3": base + grid_l3 * TICK_SIZE,
            "mid": base + grid_mid * TICK_SIZE,
            "ev_idx": np.asarray(ev_i, dtype=np.int64),
            "ev_side": np.asarray(ev_lado, dtype=np.int8),
            "ev_size": np.asarray(ev_qtd, dtype=np.int64),
            "ev_tipo": np.asarray(ev_tipo, dtype=np.int8),
            "ev_mp_pre": base + np.asarray(ev_mp_pre) * TICK_SIZE,
            "ev_mp_pos": base + np.asarray(ev_mp_pos) * TICK_SIZE,
            "ev_l3_pre": base + np.asarray(ev_l3_pre) * TICK_SIZE,
            "ev_l3_pos": base + np.asarray(ev_l3_pos) * TICK_SIZE,
            "ev_mid_pre": base + np.asarray(ev_mid_pre) * TICK_SIZE,
            "ev_mid_pos": base + np.asarray(ev_mid_pos) * TICK_SIZE,
            "ev_sp_pre": np.asarray(ev_sp_pre, dtype=np.int16),
            "ev_melhora": np.asarray(ev_melhora, dtype=np.int16),
        }
    return saida


def processar_dia(day_dir: Path):
    os.environ["POLARS_MAX_THREADS"] = str(POLARS_THREADS_PER_WORKER)
    try:
        ev = extrair_eventos_dia(day_dir)
        if ev is None:
            return None
        return replay_dia(ev)
    except Exception as exc:                                 # noqa: BLE001
        print(f"  [ERROR] {day_dir}: {type(exc).__name__}: {exc}", flush=True)
        return None


# ==============================================================================
# PART 3: VALIDATION REPORT
# ==============================================================================
def imprimir_relatorio(rep: pl.DataFrame):
    t = {c: rep[c].sum() for c in rep.columns if rep[c].dtype.is_numeric()}
    new_total = t["new_orders_total"]
    uc = t["undercut_real"]
    unchanged = t["no_spread_change_total"]

    def pct(x):
        return f"{100.0 * x / new_total:6.2f}%" if new_total else "   n/a"

    print("\n" + "=" * 78)
    print(f"UNDERCUTTING VALIDATION -- security_id {TARGET_SECURITY_ID}"
          f"  ({rep.height} trading days)")
    print("=" * 78)
    print(f"  MBO events replayed          : {t['events_total']:>14,}")
    print(f"  New limit orders (bid+ask)   : {new_total:>14,}")
    print(f"  Trades                       : {t['trade_count']:>14,}"
          f"   volume {t['trade_volume']:,}")
    print("-" * 78)
    print("  CLASSIFICATION OF EVERY NEW ORDER")
    print(f"    UNDERCUT (spread narrowed) : {uc:>14,}  {pct(uc)}"
          f"   buy {t['undercut_buy']:,} / sell {t['undercut_sell']:,}")
    print(f"      avg improvement          : {t['undercut_ticks_sum'] / uc if uc else 0:>14.3f} ticks")
    res = t["undercut_maybe_residual"]
    print(f"      of which trade-adjacent  : {res:>14,}  {pct(res)}  <- may be the")
    print(f"        residual of a partly-filled aggressive order; the feed carries")
    print(f"        no order_id on trades, so this cannot be resolved -- it is an")
    print(f"        UPPER BOUND on contamination, not a measurement.")
    print(f"    STRICT undercut (excl. those): {uc - res:>14,}  {pct(uc - res)}")
    print(f"    NO SPREAD CHANGE           : {unchanged:>14,}  {pct(unchanged)}")
    print(f"      join at best (queue)     : {t['join_best_buy'] + t['join_best_sell']:>14,}"
          f"  {pct(t['join_best_buy'] + t['join_best_sell'])}")
    print(f"      behind best (depth)      : {t['behind_best_buy'] + t['behind_best_sell']:>14,}"
          f"  {pct(t['behind_best_buy'] + t['behind_best_sell'])}")
    print(f"    AGGRESSIVE (lock/cross)    : {t['aggressive_buy'] + t['aggressive_sell']:>14,}"
          f"  {pct(t['aggressive_buy'] + t['aggressive_sell'])}")
    print(f"    NO REFERENCE (side empty)  : {t['no_reference']:>14,}  {pct(t['no_reference'])}")
    print("-" * 78)
    print("  GROUND-TRUTH CHECK (spread measured before/after each new order)")
    print(f"    spread narrowed            : {t['measured_narrowed']:>14,}")
    print(f"    spread unchanged           : {t['measured_unchanged']:>14,}")
    print(f"    spread widened             : {t['measured_widened']:>14,}  (must be 0)")
    print(f"    narrowed - undercut label  : {t['narrowed_vs_undercut_delta']:>14,}  (must be 0)")
    print(f"    new orders in locked book  : {t['new_orders_in_locked_book']:>14,}  (excluded above)")
    print("-" * 78)
    print("  OLD HEURISTIC (undercutting_analise.py logic, same input stream)")
    print(f"    naive undercut count       : {t['naive_undercut_total']:>14,}")
    print(f"    real undercut count        : {uc:>14,}")
    if uc:
        print(f"    naive / real               : {t['naive_undercut_total'] / uc:>14.2f} x")
    print("-" * 78)
    print("  DATA INTEGRITY")
    print(f"    deletes w/o known order    : {t['orphan_deletes']:>14,}")
    print(f"    changes w/o known order    : {t['orphan_changes']:>14,}")
    print(f"    new orders locking book    : {t['locked_or_crossed_new']:>14,}")
    print("=" * 78 + "\n")

    with pl.Config(tbl_rows=40, tbl_cols=12, tbl_width_chars=200):
        print(rep.select(
            "date", "new_orders_total", "undercut_real", "undercut_pct_of_new",
            "no_spread_change_total", "no_spread_change_pct_of_new",
            "measured_narrowed", "narrowed_vs_undercut_delta", "naive_undercut_total",
        ))


# ==============================================================================
# PART 4: DASHBOARD
# ==============================================================================
def _serie_preco(df):
    """Canonical price series for every chart: the book mid.

    mid = (best bid + best ask) / 2, taken from the replayed book on the last
    qualifying event of each minute (see replay_dia). The last trade is used only
    to fill minutes where no mid was sampled, then held forward. Defined once so
    the intraday and continuous charts can never drift apart.
    """
    return df["mid_price"].fillna(df["last_trade_price"]).ffill()

def gerar_dashboard(dia=None):
    """Intraday 4-panel view. `dia` defaults to the most recent day in the panel."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

    df = pd.read_parquet(ARQ_PANEL)
    df["window_brt"] = df["window_1m"] - pd.Timedelta(hours=3)   # UTC -> BRT
    dias = sorted(df["window_brt"].dt.date.unique())
    dia = dias[-1] if dia is None else pd.to_datetime(dia).date()
    print(f"Intraday panel for {dia}  ({len(dias)} day(s) available: "
          f"{dias[0]} .. {dias[-1]})")
    d = df[df["window_brt"].dt.date == dia].copy()
    d = d[(d["window_brt"].dt.time >= pd.to_datetime("09:00:00").time())
          & (d["window_brt"].dt.time <= pd.to_datetime("18:00:00").time())]

    d["net_undercut_imbalance"] = (d["undercut_buy_count"].astype(float)
                                   - d["undercut_sell_count"].astype(float))
    d["no_change_total"] = (d["join_best_buy"] + d["join_best_sell"]
                            + d["behind_best_buy"] + d["behind_best_sell"]).astype(float)
    d["price"] = _serie_preco(d)

    plt.style.use("dark_background")
    sns.set_context("paper", font_scale=1.1)
    fig, (ax1, ax2, ax3, ax4) = plt.subplots(
        4, 1, figsize=(16, 15), gridspec_kw={"height_ratios": [1, 1, 1, 1.3]}, sharex=True)
    fig.suptitle(f"Validated Undercutting vs. Spread-Neutral Order Flow ({dia})",
                 fontsize=18, fontweight="bold", color="white", y=0.965)
    fig.patch.set_facecolor("#111111")

    # PANEL 1: real undercutting, book-verified
    ax1.plot(d["window_brt"], d["undercut_buy_count"], color="lime", lw=1, label="Buy undercut (verified)")
    ax1.plot(d["window_brt"], d["undercut_sell_count"], color="red", lw=1, alpha=0.85,
             label="Sell undercut (verified)")
    ax1.set_title("Orders that REALLY narrowed the spread / minute", color="white", pad=10)
    ax1.set_ylabel("Orders / min", color="white")
    # old heuristic is ~25x larger; its own axis keeps the verified series readable
    ax1b = ax1.twinx()
    ax1b.plot(d["window_brt"], d["naive_undercut_buy"] + d["naive_undercut_sell"],
              color="orange", lw=0.8, ls="--", alpha=0.6, label="Old heuristic (right axis)")
    ax1b.set_ylabel("Old heuristic / min", color="orange")
    ax1b.tick_params(colors="orange")
    ax1b.legend(facecolor="#222222", edgecolor="white", labelcolor="white", loc="upper right")

    # PANEL 2: spread-neutral flow
    ax2.plot(d["window_brt"], d["join_best_buy"] + d["join_best_sell"],
             color="deepskyblue", lw=1, label="Join at best (queue)")
    ax2.plot(d["window_brt"], d["behind_best_buy"] + d["behind_best_sell"],
             color="violet", lw=1, alpha=0.85, label="Behind best (depth)")
    ax2.set_title("Orders that did NOT change the spread / minute", color="white", pad=10)
    ax2.set_ylabel("Orders / min", color="white")

    # PANEL 3: net imbalance + realised spread
    colors = ["lime" if v > 0 else "red" for v in d["net_undercut_imbalance"]]
    ax3.bar(d["window_brt"], d["net_undercut_imbalance"], color=colors, width=0.0006,
            label="Net imbalance (buy - sell)")
    ax3.axhline(0, color="white", ls="--", lw=1)
    ax3.set_title("Net undercutting imbalance (buy - sell)  &  average spread",
                  color="white", pad=10)
    ax3.set_ylabel("Net imbalance", color="white")
    ax3b = ax3.twinx()
    ax3b.plot(d["window_brt"], d["avg_spread_ticks"], color="gold", lw=1.2, label="Avg spread (ticks)")
    ax3b.set_ylabel("Spread (ticks)", color="gold")
    ax3b.tick_params(colors="gold")
    ax3b.legend(facecolor="#222222", edgecolor="white", labelcolor="white", loc="upper right")

    # PANEL 4: price
    ax4.plot(d["window_brt"], d["price"], color="cyan", lw=2, label="Mid price")
    ax4.set_title("Price action result (book mid)", color="white", pad=10)
    ax4.set_ylabel("Mid price (points)", color="white")
    ax4.set_xlabel("Time (BRT)", color="white")

    for ax in (ax1, ax2, ax3, ax4):
        ax.set_facecolor("#111111")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.tick_params(colors="white", rotation=45)
        ax.grid(True, color="gray", alpha=0.2, ls=":")
        ax.legend(facecolor="#222222", edgecolor="white", labelcolor="white", loc="upper left")
        for sp in ("bottom", "left"):
            ax.spines[sp].set_color("white")
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    out = IMG_PANEL.with_name(IMG_PANEL.stem + f"_{dia:%Y%m%d}.png")
    plt.savefig(out, dpi=150, facecolor=fig.get_facecolor(), edgecolor="none",
                transparent=False)
    plt.close(fig)
    print(f"[SUCCESS] Intraday chart saved: {out}")


def gerar_visao_mensal():
    """Whole-period overview: one point per trading day, plus intraday seasonality."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

    df = pd.read_parquet(ARQ_PANEL)
    df["window_brt"] = df["window_1m"] - pd.Timedelta(hours=3)
    df["dia"] = df["window_brt"].dt.date
    df["hhmm"] = df["window_brt"].dt.hour + df["window_brt"].dt.minute / 60.0
    df = df[(df["hhmm"] >= 9) & (df["hhmm"] <= 18)]

    df["undercut"] = df["undercut_buy_count"] + df["undercut_sell_count"]
    df["no_change"] = (df["join_best_buy"] + df["join_best_sell"]
                       + df["behind_best_buy"] + df["behind_best_sell"])
    df["new_orders"] = df["new_bid"] + df["new_ask"]

    por_dia = df.groupby("dia").agg(
        undercut=("undercut", "sum"),
        no_change=("no_change", "sum"),
        new_orders=("new_orders", "sum"),
        naive=("naive_undercut_buy", "sum"),
        naive2=("naive_undercut_sell", "sum"),
        spread=("avg_spread_ticks", "mean"),
    )
    por_dia["naive"] = por_dia["naive"] + por_dia["naive2"]
    por_dia["undercut_pct"] = 100.0 * por_dia["undercut"] / por_dia["new_orders"]
    por_dia["no_change_pct"] = 100.0 * por_dia["no_change"] / por_dia["new_orders"]

    # intraday seasonality, averaged over all days
    perfil = df.groupby(df["window_brt"].dt.floor("5min").dt.time).agg(
        undercut=("undercut", "mean"), no_change=("no_change", "mean"))
    perfil.index = [t.hour + t.minute / 60.0 for t in perfil.index]
    perfil = perfil.sort_index()

    plt.style.use("dark_background")
    sns.set_context("paper", font_scale=1.05)
    fig, axes = plt.subplots(2, 2, figsize=(16, 9))
    fig.patch.set_facecolor("#111111")
    fig.suptitle(
        f"Undercutting validation overview -- {por_dia.index[0]} to {por_dia.index[-1]}"
        f"  ({len(por_dia)} trading days)",
        fontsize=16, fontweight="bold", color="white")
    x = range(len(por_dia))
    lab = [d.strftime("%d/%m") for d in por_dia.index]

    ax = axes[0, 0]
    ax.bar(x, por_dia["undercut"], color="lime", alpha=0.85)
    ax.set_title("Real undercutting events per day", color="white")
    ax.set_ylabel("Orders")

    ax = axes[0, 1]
    ax.bar(x, por_dia["undercut_pct"], color="deepskyblue", alpha=0.85)
    ax.set_title("Undercutting as % of all new orders", color="white")
    ax.set_ylabel("% of new orders")

    ax = axes[1, 0]
    ax.bar(x, por_dia["naive"], color="orange", alpha=0.6, label="Old heuristic")
    ax.bar(x, por_dia["undercut"], color="lime", alpha=0.95, label="Verified")
    ax.set_yscale("log")
    ax.set_title("Old heuristic vs. book-verified count (log scale)", color="white")
    ax.set_ylabel("Orders (log)")
    ax.legend(facecolor="#222222", edgecolor="white", labelcolor="white")

    ax = axes[1, 1]
    ax.plot(perfil.index, perfil["undercut"], color="lime", lw=1.5, label="Undercut")
    ax.set_title("Intraday profile (5-min avg across all days)", color="white")
    ax.set_xlabel("Hour (BRT)")
    ax.set_ylabel("Undercuts / min", color="lime")
    axb = ax.twinx()
    axb.plot(perfil.index, perfil["no_change"], color="violet", lw=1.2, alpha=0.8)
    axb.set_ylabel("Spread-neutral / min", color="violet")
    axb.tick_params(colors="violet")

    for i, ax in enumerate(axes.ravel()):
        ax.set_facecolor("#111111")
        ax.grid(True, color="gray", alpha=0.2, ls=":")
        ax.tick_params(colors="white")
        if i < 3:
            ax.set_xticks(list(x))
            ax.set_xticklabels(lab, rotation=45, color="white")
        for sp in ("bottom", "left"):
            ax.spines[sp].set_color("white")
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    out = IMG_PANEL.with_name(IMG_PANEL.stem + "_overview.png")
    plt.savefig(out, dpi=150, facecolor=fig.get_facecolor(), transparent=False)
    plt.close(fig)
    print(f"[SUCCESS] Overview chart saved: {out}")


def gerar_serie_continua(freq: str = "1h"):
    """Continuous series across the whole period: undercuts on one axis, mid price
    on the other.

    The x-axis is a sequential bin index rather than a real timestamp, so the
    overnight gaps are collapsed and the price line stays continuous day to day.
    Day boundaries are marked with dashed separators and dated at the top.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

    df = pd.read_parquet(ARQ_PANEL)
    df["window_brt"] = df["window_1m"] - pd.Timedelta(hours=3)      # UTC -> BRT
    df = df.sort_values("window_brt").set_index("window_brt")

    # keep the regular session only, so dead overnight minutes never enter a bin
    hhmm = df.index.hour + df.index.minute / 60.0
    df = df[(hhmm >= 9) & (hhmm <= 18)]

    df["undercut"] = df["undercut_buy_count"] + df["undercut_sell_count"]
    df["net_undercut"] = df["undercut_buy_count"] - df["undercut_sell_count"]
    df["price"] = _serie_preco(df)

    binned = df.resample(freq).agg(
        undercut=("undercut", "sum"),
        undercut_buy=("undercut_buy_count", "sum"),
        undercut_sell=("undercut_sell_count", "sum"),
        net_undercut=("net_undercut", "sum"),
        price=("price", "last"),
        spread_sum=("spread_ticks_sum", "sum"),
        obs=("spread_obs", "sum"),
        bid_size=("best_bid_size_sum", "sum"),
        ask_size=("best_ask_size_sum", "sum"),
    ).dropna(subset=["price"])
    # event-weighted mean spread, not a mean of per-minute means
    binned["spread"] = binned["spread_sum"] / binned["obs"].replace(0, np.nan)
    binned = binned[binned["undercut"] > 0]           # drop empty/auction bins
    binned["dia"] = binned.index.date

    x = np.arange(len(binned))
    dias = binned["dia"].values
    # first bin of each day -> day separator
    bordas = [i for i in range(1, len(dias)) if dias[i] != dias[i - 1]]

    # liquidity pressure at the touch: resting size on each side, averaged over
    # the events sampled in each bin
    obs = binned["obs"].replace(0, np.nan)
    binned["bid_depth"] = binned["bid_size"] / obs
    binned["ask_depth"] = binned["ask_size"] / obs
    tot_depth = binned["bid_depth"] + binned["ask_depth"]
    # +1 = buy side dominates the top of book, -1 = sell side dominates
    binned["pressure"] = (binned["bid_depth"] - binned["ask_depth"]) / tot_depth

    plt.style.use("dark_background")
    sns.set_context("paper", font_scale=1.15)
    fig, (ax, axp, axs) = plt.subplots(
        3, 1, figsize=(20, 14), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.2, 1.0], "hspace": 0.12})
    fig.patch.set_facecolor("#111111")
    for a_ in (ax, axp, axs):
        a_.set_facecolor("#111111")

    # LEFT AXIS: undercuts per bin, split by side
    ax.bar(x, binned["undercut_buy"], color="lime", alpha=0.65, width=0.85,
           label="Buy undercuts")
    ax.bar(x, binned["undercut_sell"], bottom=binned["undercut_buy"],
           color="red", alpha=0.6, width=0.85, label="Sell undercuts")
    ax.set_ylabel(f"Verified undercuts per {freq}", color="lime", fontsize=12)
    ax.tick_params(axis="y", colors="lime")
    ax.set_ylim(0, binned["undercut"].max() * 2.1)   # keep bars in the lower half

    # RIGHT AXIS: mid price
    ax2 = ax.twinx()
    ax2.plot(x, binned["price"], color="cyan", lw=2.0, label="Mid price")
    ax2.set_ylabel("Mid price (points)", color="cyan", fontsize=12)
    ax2.tick_params(axis="y", colors="cyan")
    pmin, pmax = binned["price"].min(), binned["price"].max()
    pad = (pmax - pmin) * 0.08
    ax2.set_ylim(pmin - pad, pmax + pad)

    # ---- PANEL 2: liquidity pressure at the top of book -------------------
    press = binned["pressure"].values
    axp.fill_between(x, 0, np.where(press > 0, press, 0), color="lime", alpha=0.75,
                     step="mid", label="Buy side dominant")
    axp.fill_between(x, 0, np.where(press < 0, press, 0), color="red", alpha=0.7,
                     step="mid", label="Sell side dominant")
    axp.axhline(0, color="white", lw=1.0, alpha=0.7)
    axp.set_ylabel("Book pressure\n(bid - ask) / total", color="white", fontsize=11)
    lim = float(np.nanmax(np.abs(press))) * 1.15
    axp.set_ylim(-lim, lim)
    axp.legend(facecolor="#222222", edgecolor="white", labelcolor="white",
               loc="upper right", ncol=2, fontsize=9)

    # ---- PANEL 3: spread length -------------------------------------------
    axs.plot(x, binned["spread"], color="gold", lw=1.6, label="Mean spread")
    axs.axhline(1.0, color="white", ls=":", lw=1.0, alpha=0.6)
    axs.text(len(binned) * 0.002, 1.0, " 1 tick floor", color="white", fontsize=8,
             va="bottom", alpha=0.7)
    axs.set_ylabel("Spread (ticks)", color="gold", fontsize=11)
    axs.tick_params(axis="y", colors="gold")
    axs.set_ylim(bottom=0.98)
    axs.legend(facecolor="#222222", edgecolor="white", labelcolor="white",
               loc="upper right", fontsize=9)

    # ---- shared decoration -------------------------------------------------
    horas = [i for i, ts in enumerate(binned.index) if ts.hour in (9, 12, 15)]
    for a_ in (ax, axp, axs):
        for b in bordas:
            a_.axvline(b - 0.5, color="white", ls="--", lw=0.9, alpha=0.45)
        a_.set_xlim(-1, len(binned))
        a_.grid(True, axis="y", color="gray", alpha=0.15, ls=":")
        for sp in ("top", "right"):
            a_.spines[sp].set_visible(False)
        for sp in ("bottom", "left"):
            a_.spines[sp].set_color("white")
    for sp in ("top", "right"):
        ax2.spines[sp].set_visible(False)

    # date labels across the top panel only
    for d in pd.unique(dias):
        idx = np.where(dias == d)[0]
        ax.text(idx.mean(), ax.get_ylim()[1] * 0.985, pd.Timestamp(d).strftime("%d/%m"),
                ha="center", va="top", color="white", fontsize=10, fontweight="bold")

    axs.set_xticks(horas)
    axs.set_xticklabels([binned.index[i].strftime("%Hh") for i in horas],
                        rotation=0, fontsize=8, color="white")
    axs.set_xlabel("Session time (BRT) -- overnight gaps removed", color="white")

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    # above the axes, so it never covers the date labels along the top
    ax.legend(h1 + h2, l1 + l2, facecolor="#111111", edgecolor="#111111",
              labelcolor="white", loc="lower right", bbox_to_anchor=(1.0, 1.0),
              ncol=3, fontsize=9, frameon=False)

    d0, d1 = binned.index[0].date(), binned.index[-1].date()
    fig.suptitle(f"Undercutting, book pressure and spread -- continuous {freq} series "
                 f"({d0} to {d1})",
                 fontsize=17, fontweight="bold", color="white", y=0.98)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    out = IMG_PANEL.with_name(IMG_PANEL.stem + f"_continuo_{freq}.png")
    plt.savefig(out, dpi=150, facecolor=fig.get_facecolor(), transparent=False)
    plt.close(fig)
    print(f"[SUCCESS] Continuous series saved: {out}")
    return out


def gerar_frequencia_spread(bin_min: int = 5):
    """Frequency of each raw spread length across the clock.

    Two weightings, because they answer different questions:
      * TIME-weighted  -- share of wall-clock time the book sits at each spread.
                          This is the state of the market.
      * EVENT-weighted -- share of order arrivals that see each spread. This is
                          what a participant experiences, and it is the basis of
                          `avg_spread_ticks`.
    They diverge whenever wide-spread moments attract more order traffic per
    second than tight ones, which is exactly what happens here.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

    df = pd.read_parquet(ARQ_PANEL)
    df["brt"] = df["window_1m"] - pd.Timedelta(hours=3)
    hh = df["brt"].dt.hour + df["brt"].dt.minute / 60.0
    df = df[(hh >= 9) & (hh <= 18)].copy()
    n_dias = df["brt"].dt.date.nunique()

    # fold every day onto one clock, in `bin_min` buckets
    df["tod"] = (df["brt"].dt.hour * 60 + df["brt"].dt.minute) // bin_min * bin_min
    cols_ms = ["spread_ms_1", "spread_ms_2", "spread_ms_3", "spread_ms_4p"]
    cols_n = ["spread_n_1", "spread_n_2", "spread_n_3", "spread_n_4p"]
    g = df.groupby("tod")[cols_ms + cols_n + ["spread_ms_dropped"]].sum()
    g = g[(g[cols_ms].sum(axis=1) > 0) & (g[cols_n].sum(axis=1) > 0)]

    share_t = 100.0 * g[cols_ms].div(g[cols_ms].sum(axis=1), axis=0)
    share_n = 100.0 * g[cols_n].div(g[cols_n].sum(axis=1), axis=0)
    x = g.index / 60.0                                   # hours since midnight
    rotulos = ["1 tick", "2 ticks", "3 ticks", "4+ ticks"]
    cores = ["#1f9d55", "gold", "orangered", "magenta"]

    mean_t = sum((i + 1) * g[c] for i, c in enumerate(cols_ms)) / g[cols_ms].sum(axis=1)
    mean_n = sum((i + 1) * g[c] for i, c in enumerate(cols_n)) / g[cols_n].sum(axis=1)

    plt.style.use("dark_background")
    sns.set_context("paper", font_scale=1.1)
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(16, 13), sharex=True,
                                     gridspec_kw={"hspace": 0.16})
    fig.patch.set_facecolor("#111111")
    fig.suptitle(
        f"Raw spread length by clock time -- {bin_min}-min buckets, "
        f"{n_dias} trading days pooled",
        fontsize=17, fontweight="bold", color="white", y=0.975)

    # PANEL 1: full composition of clock time (zoomed: 1 tick dominates)
    a1.stackplot(x, [share_t[c] for c in cols_ms], labels=rotulos, colors=cores, alpha=0.9)
    lo = float(share_t["spread_ms_1"].min())
    a1.set_ylim(max(0, lo - 1.0), 100)
    a1.set_ylabel("% of clock time", color="white")
    a1.set_title("Share of wall-clock time at each spread length "
                 "(y-axis zoomed -- 1 tick fills the rest)", color="white", pad=8)
    a1.legend(facecolor="#222222", edgecolor="white", labelcolor="white",
              loc="lower left", ncol=4, fontsize=9)

    # PANEL 2: the wide-book signal, both weightings
    wide_t = share_t[["spread_ms_2", "spread_ms_3", "spread_ms_4p"]].sum(axis=1)
    wide_n = share_n[["spread_n_2", "spread_n_3", "spread_n_4p"]].sum(axis=1)
    a2.fill_between(x, 0, wide_n, color="deepskyblue", alpha=0.30)
    a2.plot(x, wide_n, color="deepskyblue", lw=1.8, label="Event-weighted (orders arriving)")
    a2.plot(x, wide_t, color="gold", lw=1.8, label="Time-weighted (clock)")
    a2.set_ylabel("% wider than 1 tick", color="white")
    a2.set_title("How often the book is wider than 1 tick -- the window in which "
                 "undercutting is even possible", color="white", pad=8)
    a2.legend(facecolor="#222222", edgecolor="white", labelcolor="white",
              loc="upper right", fontsize=9)

    # PANEL 3: mean spread under both weightings
    a3.plot(x, mean_n, color="deepskyblue", lw=1.8, label="Event-weighted mean")
    a3.plot(x, mean_t, color="gold", lw=1.8, label="Time-weighted mean")
    a3.axhline(1.0, color="white", ls=":", lw=1.0, alpha=0.6)
    a3.set_ylabel("Mean spread (ticks)", color="white")
    a3.set_xlabel("Time of day (BRT)", color="white")
    a3.set_title("Mean spread -- the gap between the two lines is the sampling bias",
                 color="white", pad=8)
    a3.legend(facecolor="#222222", edgecolor="white", labelcolor="white",
              loc="upper right", fontsize=9)

    for a_ in (a1, a2, a3):
        a_.set_facecolor("#111111")
        a_.grid(True, color="gray", alpha=0.18, ls=":")
        a_.tick_params(colors="white")
        a_.set_xlim(9, 18)
        a_.set_xticks(range(9, 19))
        a_.set_xticklabels([f"{h}h" for h in range(9, 19)], color="white")
        for sp in ("top", "right"):
            a_.spines[sp].set_visible(False)
        for sp in ("bottom", "left"):
            a_.spines[sp].set_color("white")

    plt.tight_layout(rect=[0, 0, 1, 0.955])
    out = IMG_PANEL.with_name(IMG_PANEL.stem + "_spread_freq.png")
    plt.savefig(out, dpi=150, facecolor=fig.get_facecolor(), transparent=False)
    plt.close(fig)

    tt, tn = g[cols_ms].sum(), g[cols_n].sum()
    print(f"[SUCCESS] Spread frequency chart saved: {out}")
    print("  time-weighted  %: " + "  ".join(
        f"{r}={100*v/tt.sum():.3f}" for r, v in zip(rotulos, tt)))
    print("  event-weighted %: " + "  ".join(
        f"{r}={100*v/tn.sum():.3f}" for r, v in zip(rotulos, tn)))
    print(f"  clock time accounted: {tt.sum()/60000:,.0f} min  "
          f"(dropped as gaps: {g['spread_ms_dropped'].sum()/60000:,.1f} min)")
    return out


def gerar_serie_segundo(dia: str | None = None, h_ini: float = 9.0,
                        h_fim: float = 18.0):
    """Same three panels as the continuous chart, but binned per SECOND.

    Replays one day from the event cache at 1-second grain. The measurement is
    identical to the 1-minute version -- same book, same classification -- only
    the accumulator grid is finer, so nothing here is an interpolation of the
    hourly series.

    A whole month at 1s would be ~4.7M points, far more than a chart has pixels,
    so this works on one day and takes an optional hour window to narrow further.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

    cache = sorted(OUTPUT_DIR.glob(f"undercut_valid_events_{TARGET_SECURITY_ID}_*.parquet"))
    if not cache:
        raise FileNotFoundError("no event cache -- run the extraction first")
    if dia is None:
        alvo = cache[-1]
    else:
        alvo = OUTPUT_DIR / f"undercut_valid_events_{TARGET_SECURITY_ID}_{dia}.parquet"
        if not alvo.exists():
            raise FileNotFoundError(
                f"{alvo.name} not cached. Available: "
                f"{[c.stem.split('_')[-1] for c in cache]}")

    print(f"Replaying {alvo.stem.split('_')[-1]} at 1-second grain...")
    res = replay_dia(alvo, bucket="1s")
    if res is None:
        raise RuntimeError(f"no events in {alvo.name}")

    d = res["minutes"].to_pandas()
    d["brt"] = d["window_1m"] - pd.Timedelta(hours=3)
    hh = d["brt"].dt.hour + d["brt"].dt.minute / 60.0 + d["brt"].dt.second / 3600.0
    d = d[(hh >= h_ini) & (hh <= h_fim)].sort_values("brt").copy()

    d["undercut_buy"] = d["undercut_buy_count"].astype(float)
    d["undercut_sell"] = d["undercut_sell_count"].astype(float)
    d["price"] = _serie_preco(d)
    obs = d["spread_obs"].replace(0, np.nan)
    d["spread"] = (d["spread_ticks_sum"] / obs).ffill()
    bid_d = d["best_bid_size_sum"] / obs
    ask_d = d["best_ask_size_sum"] / obs
    d["pressure"] = ((bid_d - ask_d) / (bid_d + ask_d)).ffill()

    t = d["brt"]
    dia_txt = d["brt"].dt.date.iloc[0]
    quietos = int((d["spread_obs"] == 0).sum())

    plt.style.use("dark_background")
    sns.set_context("paper", font_scale=1.15)
    fig, (ax, axp, axs) = plt.subplots(
        3, 1, figsize=(20, 14), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.2, 1.0], "hspace": 0.12})
    fig.patch.set_facecolor("#111111")
    for a_ in (ax, axp, axs):
        a_.set_facecolor("#111111")

    # PANEL 1: undercuts per second, stacked, + mid price
    ax.fill_between(t, 0, d["undercut_buy"], color="lime", alpha=0.7, lw=0,
                    label="Buy undercuts/s")
    ax.fill_between(t, d["undercut_buy"], d["undercut_buy"] + d["undercut_sell"],
                    color="red", alpha=0.6, lw=0, label="Sell undercuts/s")
    ax.set_ylabel("Verified undercuts per second", color="lime", fontsize=12)
    ax.tick_params(axis="y", colors="lime")
    ax.set_ylim(0, float((d["undercut_buy"] + d["undercut_sell"]).max()) * 2.1)

    ax2 = ax.twinx()
    ax2.plot(t, d["price"], color="cyan", lw=1.4, label="Mid price")
    ax2.set_ylabel("Mid price (points)", color="cyan", fontsize=12)
    ax2.tick_params(axis="y", colors="cyan")
    pmin, pmax = d["price"].min(), d["price"].max()
    pad = (pmax - pmin) * 0.08
    ax2.set_ylim(pmin - pad, pmax + pad)

    # PANEL 2: book pressure
    pr = d["pressure"].values
    axp.fill_between(t, 0, np.where(pr > 0, pr, 0), color="lime", alpha=0.75, lw=0,
                     label="Buy side dominant")
    axp.fill_between(t, 0, np.where(pr < 0, pr, 0), color="red", alpha=0.7, lw=0,
                     label="Sell side dominant")
    axp.axhline(0, color="white", lw=1.0, alpha=0.7)
    axp.set_ylabel("Book pressure\n(bid - ask) / total", color="white", fontsize=11)
    axp.legend(facecolor="#222222", edgecolor="white", labelcolor="white",
               loc="upper right", ncol=2, fontsize=9)

    # PANEL 3: spread
    axs.plot(t, d["spread"], color="gold", lw=0.8)
    axs.axhline(1.0, color="white", ls=":", lw=1.0, alpha=0.6)
    axs.set_ylabel("Spread (ticks)", color="gold", fontsize=11)
    axs.tick_params(axis="y", colors="gold")
    axs.set_ylim(bottom=0.98)
    axs.set_xlabel("Time (BRT)", color="white")

    for a_ in (ax, axp, axs):
        a_.grid(True, axis="y", color="gray", alpha=0.15, ls=":")
        a_.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        a_.tick_params(axis="x", colors="white")
        for sp in ("top", "right"):
            a_.spines[sp].set_visible(False)
        for sp in ("bottom", "left"):
            a_.spines[sp].set_color("white")
    for sp in ("top", "right"):
        ax2.spines[sp].set_visible(False)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, facecolor="#111111", edgecolor="#111111",
              labelcolor="white", loc="lower right", bbox_to_anchor=(1.0, 1.0),
              ncol=3, fontsize=9, frameon=False)

    fig.suptitle(f"Undercutting, book pressure and spread -- 1-SECOND series "
                 f"({dia_txt}  {h_ini:g}h-{h_fim:g}h BRT)",
                 fontsize=17, fontweight="bold", color="white", y=0.98)
    plt.tight_layout(rect=[0, 0, 1, 0.95])

    jan = "" if (h_ini, h_fim) == (9.0, 18.0) else f"_{h_ini:g}h{h_fim:g}h"
    out = IMG_PANEL.with_name(IMG_PANEL.stem + f"_1s_{dia_txt:%Y%m%d}{jan}.png")
    plt.savefig(out, dpi=150, facecolor=fig.get_facecolor(), transparent=False)
    plt.close(fig)
    print(f"[SUCCESS] 1-second series saved: {out}")
    print(f"  {len(d):,} seconds plotted; {quietos:,} ({100*quietos/len(d):.1f}%) "
          f"had no book event -- spread and pressure held forward there")
    return out


def _resolver_evento_dia(dia: str | None):
    """Locate the cached event file for a day (default: the most recent)."""
    cache = sorted(OUTPUT_DIR.glob(f"undercut_valid_events_{TARGET_SECURITY_ID}_*.parquet"))
    if not cache:
        raise FileNotFoundError("no event cache -- run the extraction first")
    alvo = cache[-1] if dia is None else (
        OUTPUT_DIR / f"undercut_valid_events_{TARGET_SECURITY_ID}_{dia}.parquet")
    if not alvo.exists():
        raise FileNotFoundError(
            f"{alvo.name} not cached. Available: {[c.stem.split('_')[-1] for c in cache]}")
    return alvo, alvo.stem.split("_")[-1]


def _gravar_janela(alvo: Path, ini_ms: int, fim_ms: int):
    """Replay a day ONCE, keeping every book event inside [ini_ms, fim_ms].

    Recording the whole span in a single pass is what makes a multi-chart series
    affordable -- one replay feeds every chart, instead of one replay per chart.
    """
    import pandas as pd

    res = replay_dia(alvo, bucket="1s", rec_ini_ms=ini_ms, rec_fim_ms=fim_ms)
    if res is None or not res["events"]:
        raise RuntimeError(f"no events recorded in {alvo.name} for that window")
    b, tk = res["tick_base"], res["tick_size"]
    e = pd.DataFrame(res["events"],
                     columns=["ms", "action", "side", "tick", "bb", "ba", "lbl", "size"])
    e["bid"] = b + e["bb"] * tk
    e["ask"] = b + e["ba"] * tk
    e["px"] = b + e["tick"] * tk
    e["spread"] = e["ba"] - e["bb"]
    e.loc[e["bb"] < 0, ["bid", "spread"]] = np.nan
    return e


def _plot_sequencia(e, inicio_ms: int, dur_s: float, data_txt: str, out: Path,
                    mostrar_join: bool = True, titulo_extra: str = ""):
    """Draw one spread-corridor chart from an already-recorded event frame."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    e = e.copy()
    e["t"] = (e["ms"] - inicio_ms) / 1000.0
    novos = e[e["action"] == 0]

    # Trades (action 9). Which side was the AGGRESSOR is read off the print:
    #   printed at the ask -> a buyer crossed the spread and lifted the offer
    #   printed at the bid -> a seller crossed and hit the bid
    trades = e[e["action"] == 9].copy()
    trades["agressor"] = np.where(trades["tick"] >= trades["ba"], "buy",
                          np.where(trades["tick"] <= trades["bb"], "sell", "inside"))

    # BRT clock for the title
    t0 = pd.Timestamp(data_txt) + pd.Timedelta(milliseconds=inicio_ms) - pd.Timedelta(hours=3)

    plt.style.use("dark_background")
    sns.set_context("paper", font_scale=1.15)
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(20, 13), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1, 1.1],
                                                  "hspace": 0.1})
    fig.patch.set_facecolor("#111111")
    for a_ in (a1, a2, a3):
        a_.set_facecolor("#111111")

    # ---- the spread corridor ----------------------------------------------
    a1.fill_between(e["t"], e["bid"], e["ask"], step="post",
                    color="#2a3f5f", alpha=0.55, lw=0, label="Spread (bid-ask gap)")
    a1.step(e["t"], e["bid"], where="post", color="lime", lw=1.6, label="Best bid")
    a1.step(e["t"], e["ask"], where="post", color="red", lw=1.6, label="Best ask")

    # ---- every new order, at the price it landed --------------------------
    novos = e[e["action"] == 0]
    if mostrar_join:
        jb = novos[novos["lbl"] == 2]
        a1.scatter(jb["t"], jb["px"], s=7, c="#7f8c9a", alpha=0.5, marker="o",
                   linewidths=0, zorder=3)
    ucb = novos[(novos["lbl"] == 1) & (novos["side"] == 0)]
    ucs = novos[(novos["lbl"] == 1) & (novos["side"] == 1)]
    n_uc = len(ucb) + len(ucs)
    ms_ = 190 if n_uc <= 40 else (90 if n_uc <= 150 else 35)
    a1.scatter(ucb["t"], ucb["px"], s=ms_, c="lime", marker="^", zorder=6,
               edgecolors="white", linewidths=0.8)
    a1.scatter(ucs["t"], ucs["px"], s=ms_, c="red", marker="v", zorder=6,
               edgecolors="white", linewidths=0.8)

    # aggressive NEW orders (lbl 4) -- rare: an order posted at or through the
    # opposite touch, locking or crossing the book
    agr = novos[novos["lbl"] == 4]
    a1.scatter(agr["t"], agr["px"], s=150, c="none", marker="s", zorder=7,
               edgecolors="white", linewidths=1.6)

    # trades: the aggressor consuming the other side
    tb = trades[trades["agressor"] == "buy"]
    ts_ = trades[trades["agressor"] == "sell"]
    a1.scatter(tb["t"], tb["px"], s=np.clip(tb["size"] * 6, 30, 400), c="cyan",
               marker="X", zorder=8, edgecolors="white", linewidths=0.7, alpha=0.95)
    a1.scatter(ts_["t"], ts_["px"], s=np.clip(ts_["size"] * 6, 30, 400), c="magenta",
               marker="X", zorder=8, edgecolors="white", linewidths=0.7, alpha=0.95)

    a1.set_ylabel("Price (points)", color="white", fontsize=12)
    a1.set_title(
        f"Sequence of undercutting events -- {t0:%Y-%m-%d %H:%M:%S.%f}"[:-3] +
        f" BRT +{dur_s:g}s   ({len(novos):,} new orders, "
        f"{len(ucb) + len(ucs)} undercuts){titulo_extra}",
        color="white", pad=10)

    handles = [
        Line2D([], [], color="lime", lw=2, label="Best bid"),
        Line2D([], [], color="red", lw=2, label="Best ask"),
        Patch(facecolor="#2a3f5f", alpha=0.8, label="Spread"),
        Line2D([], [], color="lime", marker="^", ls="", ms=11, mec="white",
               label="BUY undercut (spread cut)"),
        Line2D([], [], color="red", marker="v", ls="", ms=11, mec="white",
               label="SELL undercut (spread cut)"),
    ]
    if mostrar_join:
        handles.append(Line2D([], [], color="#7f8c9a", marker="o", ls="", ms=5,
                              label="Order joining the best (no spread change)"))
    handles += [
        Line2D([], [], color="cyan", marker="X", ls="", ms=11, mec="white",
               label="TRADE - buyer lifted the ask (aggressive buy)"),
        Line2D([], [], color="magenta", marker="X", ls="", ms=11, mec="white",
               label="TRADE - seller hit the bid (aggressive sell)"),
        Line2D([], [], color="none", marker="s", ls="", ms=11, mec="white",
               label="New order locking/crossing the book"),
    ]
    a1.legend(handles=handles, facecolor="#1a1a1a", edgecolor="white",
              labelcolor="white", loc="upper left", fontsize=9, ncol=3)

    # ---- spread width, in ticks -------------------------------------------
    a2.step(e["t"], e["spread"], where="post", color="gold", lw=1.5)
    a2.fill_between(e["t"], 1, e["spread"], step="post", color="gold", alpha=0.25, lw=0)
    a2.axhline(1, color="white", ls=":", lw=1, alpha=0.6)
    marcas = pd.concat([ucb, ucs])
    if len(marcas) <= 40:                    # otherwise they merge into a wall
        for _, r in marcas.iterrows():
            a2.axvline(r["t"], color="white", ls="-", lw=0.6, alpha=0.35)
    a2.set_ylabel("Spread (ticks)", color="gold", fontsize=12)
    a2.set_ylim(bottom=0.9)
    a2.tick_params(axis="y", colors="gold")

    # ---- traded volume, signed by which side crossed the spread -----------
    if len(trades):
        larg = max(dur_s / 600.0, 1e-5)
        a3.bar(tb["t"], tb["size"], width=larg, color="cyan", alpha=0.9,
               label="Aggressive BUY volume")
        a3.bar(ts_["t"], -ts_["size"], width=larg, color="magenta", alpha=0.9,
               label="Aggressive SELL volume")
        a3.legend(facecolor="#222222", edgecolor="white", labelcolor="white",
                  loc="upper right", ncol=2, fontsize=9)
    a3.axhline(0, color="white", lw=1, alpha=0.7)
    a3.set_ylabel("Traded qty\n(buy + / sell -)", color="white", fontsize=11)
    a3.set_xlabel(f"Seconds from {t0:%H:%M:%S} BRT", color="white")

    for a_ in (a1, a2, a3):
        a_.grid(True, color="gray", alpha=0.15, ls=":")
        a_.tick_params(axis="x", colors="white")
        a_.set_xlim(0, dur_s)
        for sp in ("top", "right"):
            a_.spines[sp].set_visible(False)
        for sp in ("bottom", "left"):
            a_.spines[sp].set_color("white")

    plt.tight_layout()

    plt.savefig(out, dpi=150, facecolor=fig.get_facecolor(), transparent=False)
    plt.close(fig)
    return {"file": out, "eventos": len(e), "novos": len(novos),
            "undercut_buy": len(ucb), "undercut_sell": len(ucs),
            "trades": len(trades), "volume": int(trades["size"].sum()) if len(trades) else 0,
            "agr_buy": int((trades["agressor"] == "buy").sum()) if len(trades) else 0,
            "agr_sell": int((trades["agressor"] == "sell").sum()) if len(trades) else 0,
            "spread_min": float(e["spread"].min()), "spread_max": float(e["spread"].max())}


def gerar_sequencia_undercut(dia: str | None = None, inicio_ms: int | None = None,
                             dur_s: float = 0.15, mostrar_join: bool = True):
    """Event-by-event view of undercutting: the spread corridor collapsing.

    Draws the book as a corridor -- best bid and best ask as step lines with the
    spread shaded between them -- marks every new order at the price where it
    landed, and marks each trade with the side that crossed the spread.

    `inicio_ms` is ms since midnight UTC. If omitted, the busiest undercutting
    second of the day is found first and the window is centred on it.
    """
    alvo, data_txt = _resolver_evento_dia(dia)

    if inicio_ms is None:
        print("Finding the busiest undercutting second...")
        base = replay_dia(alvo, bucket="1s")
        m = base["minutes"].to_pandas()
        m["uc"] = m["undercut_buy_count"] + m["undercut_sell_count"]
        pico = m.loc[m["uc"].idxmax(), "window_1m"]
        inicio_ms = int(pico.hour * 3_600_000 + pico.minute * 60_000
                        + pico.second * 1_000) - int(dur_s * 1000 / 2)
        print(f"  peak second: {pico.time()} UTC with {int(m['uc'].max())} undercuts")

    print(f"Replaying {data_txt}, recording {inicio_ms/3.6e6:.4f}h .. +{dur_s}s ...")
    e = _gravar_janela(alvo, inicio_ms, inicio_ms + int(dur_s * 1000))
    out = IMG_PANEL.with_name(IMG_PANEL.stem + f"_sequencia_{data_txt}_{inicio_ms}.png")
    st = _plot_sequencia(e, inicio_ms, dur_s, data_txt, out, mostrar_join)

    print(f"[SUCCESS] Event sequence saved: {out}")
    print(f"  {st['eventos']:,} book events in {dur_s:g}s | {st['novos']:,} new orders | "
          f"{st['undercut_buy']} buy + {st['undercut_sell']} sell undercuts | "
          f"spread {st['spread_min']:.0f}-{st['spread_max']:.0f} ticks")
    if st["trades"]:
        print(f"  trades: {st['trades']} ({st['agr_buy']} buyer-initiated, "
              f"{st['agr_sell']} seller-initiated) | volume {st['volume']:,}")
    n_uc = st["undercut_buy"] + st["undercut_sell"]
    if n_uc > 60:
        print(f"  NOTE: {n_uc} undercuts in view -- pass a smaller dur_s "
              f"(try {dur_s * 40 / n_uc:.3f}) to read individual events")
    return out


def gerar_sequencia_minuto(dia: str | None = None, inicio_ms: int | None = None,
                           dur_s: float = 1.0, total_s: float = 60.0,
                           mostrar_join: bool = False):
    """Cover a span (default one minute) with a series of spread-corridor charts.

    One replay records the whole span, then it is sliced into `dur_s` chunks and
    each chunk is drawn as its own chart -- so the cost is a single replay, not
    one per chart. Files are numbered in time order in their own directory, and a
    summary CSV lists what is in each frame so you can jump to the interesting
    ones instead of opening all of them.
    """
    import pandas as pd

    alvo, data_txt = _resolver_evento_dia(dia)

    if inicio_ms is None:
        print("Finding the busiest undercutting minute...")
        base = replay_dia(alvo, bucket="1m")
        m = base["minutes"].to_pandas()
        m["uc"] = m["undercut_buy_count"] + m["undercut_sell_count"]
        pico = m.loc[m["uc"].idxmax(), "window_1m"]
        inicio_ms = int(pico.hour * 3_600_000 + pico.minute * 60_000)
        print(f"  peak minute: {pico.time()} UTC with {int(m['uc'].max()):,} undercuts")

    n = int(np.ceil(total_s / dur_s))
    print(f"Recording {total_s:g}s from {inicio_ms/3.6e6:.4f}h in ONE replay, "
          f"then slicing into {n} charts of {dur_s:g}s...")
    e = _gravar_janela(alvo, inicio_ms, inicio_ms + int(total_s * 1000))

    destino = IMG_PANEL.parent / f"sequencia_{data_txt}_{inicio_ms}_{dur_s:g}s"
    destino.mkdir(exist_ok=True)

    t0 = pd.Timestamp(data_txt) + pd.Timedelta(milliseconds=inicio_ms) - pd.Timedelta(hours=3)
    linhas = []
    for k in range(n):
        ini = inicio_ms + int(k * dur_s * 1000)
        fim = ini + int(dur_s * 1000)
        sub = e[(e["ms"] >= ini) & (e["ms"] < fim)]
        if sub.empty:
            print(f"  [{k+1:>3}/{n}] empty window, skipped")
            continue
        # carry the last event before the chunk so the step lines start at the
        # correct level instead of at the first event inside the chunk
        antes = e[e["ms"] < ini]
        if len(antes):
            borda = antes.iloc[[-1]].copy()
            borda["ms"] = ini
            borda["action"] = -1               # not a real event: corridor seed only
            sub = pd.concat([borda, sub], ignore_index=True)

        carimbo = (t0 + pd.Timedelta(seconds=k * dur_s)).strftime("%H%M%S_%f")[:-3]
        out = destino / f"{k:03d}_{carimbo}.png"
        st = _plot_sequencia(sub, ini, dur_s, data_txt, out, mostrar_join,
                             titulo_extra=f"   [{k+1}/{n}]")
        st["frame"] = k
        st["hora_brt"] = (t0 + pd.Timedelta(seconds=k * dur_s)).strftime("%H:%M:%S.%f")[:-3]
        linhas.append(st)
        print(f"  [{k+1:>3}/{n}] {st['hora_brt']}  "
              f"{st['undercut_buy'] + st['undercut_sell']:>4} undercuts  "
              f"{st['trades']:>4} trades  vol {st['volume']:>6,}  "
              f"spread {st['spread_min']:.0f}-{st['spread_max']:.0f}")

    resumo = pd.DataFrame(linhas)
    resumo["file"] = resumo["file"].astype(str)
    csv = destino / "resumo.csv"
    resumo.to_csv(csv, index=False)
    tot_uc = int((resumo["undercut_buy"] + resumo["undercut_sell"]).sum())
    print(f"\n[SUCCESS] {len(resumo)} charts in {destino}")
    print(f"  {tot_uc:,} undercuts and {int(resumo['trades'].sum()):,} trades "
          f"over {total_s:g}s | summary: {csv.name}")
    pico = resumo.loc[(resumo["undercut_buy"] + resumo["undercut_sell"]).idxmax()]
    print(f"  busiest frame: {pico['frame']:.0f} at {pico['hora_brt']} with "
          f"{int(pico['undercut_buy'] + pico['undercut_sell'])} undercuts")
    return destino


def _achar_ffmpeg() -> str | None:
    """Locate an ffmpeg binary: system PATH first, then a pip-installed one."""
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def gerar_filme(dia: str | None = None, inicio_ms: int | None = None,
                total_s: float = 8.0, janela_s: float = 1.5, fps: int = 20,
                velocidade: float = 1.0, largura_px: int = 1280,
                formato: str = "auto", mostrar_trades: bool = True,
                mostrar_volume: bool = True, mostrar_ambiguos: bool = True,
                saida: Path | None = None):
    """Render the book dynamics as an actual movie.

    The chart series looks like chapters rather than film because each frame is
    an independent panel: its own x-range, its own auto-scaled y-axis, nothing
    shared between consecutive images. A movie needs the opposite --

      * a WINDOW that SLIDES by a fraction of its own width each frame, so
        successive frames overlap almost completely and the eye reads motion;
      * a CAMERA that pans smoothly -- the y-range follows the price through a
        rolling mean instead of snapping to each window's min/max;
      * enough frames per second (20+) that the motion is continuous.

    So: one replay records the span, everything is drawn once, and each frame
    only moves the viewport. `total_s` is market time covered.

    `velocidade` is playback speed: 1.0 = real time, 0.25 = quarter-speed slow
    motion (4x the frames, 4x the render time and file size). At this event
    density real time is hard to follow, so 0.25-0.5 usually reads better.

    `formato`: "mp4" (true colour, ~10x smaller, seekable), "gif" (no external
    dependency), or "auto" -- mp4 when an ffmpeg binary can be found, else gif.

    Trades outnumber undercuts by roughly 10:1, so their markers dominate the
    frame. Set `mostrar_trades=False` (and usually `mostrar_volume=False`, which
    drops the third panel) for a clean undercutting-only film: just the corridor,
    the orders that cut into it, and the spread.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.animation as animation
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    alvo, data_txt = _resolver_evento_dia(dia)
    if inicio_ms is None:
        print("Finding the busiest undercutting second...")
        base = replay_dia(alvo, bucket="1s")
        m = base["minutes"].to_pandas()
        m["uc"] = m["undercut_buy_count"] + m["undercut_sell_count"]
        pico = m.loc[m["uc"].idxmax(), "window_1m"]
        inicio_ms = int(pico.hour * 3_600_000 + pico.minute * 60_000
                        + pico.second * 1_000) - int(total_s * 1000 / 2)
        print(f"  peak second: {pico.time()} UTC with {int(m['uc'].max())} undercuts")

    print(f"Recording {total_s:g}s in one replay...")
    e = _gravar_janela(alvo, inicio_ms, inicio_ms + int(total_s * 1000))
    e = e.reset_index(drop=True)
    e["t"] = (e["ms"] - inicio_ms) / 1000.0

    novos = e[e["action"] == 0]
    # lbl 1 = STRICT undercut; lbl 6 = trade-adjacent same-side (ambiguous, may be
    # the residual of a partly-filled aggressive order -- see replay_dia)
    ucb = novos[(novos["lbl"] == 1) & (novos["side"] == 0)]
    ucs = novos[(novos["lbl"] == 1) & (novos["side"] == 1)]
    amb = novos[novos["lbl"] == 6]
    trades = e[e["action"] == 9].copy()
    trades["agressor"] = np.where(trades["tick"] >= trades["ba"], "buy",
                          np.where(trades["tick"] <= trades["bb"], "sell", "inside"))
    tb = trades[trades["agressor"] == "buy"]
    ts_ = trades[trades["agressor"] == "sell"]

    t0 = pd.Timestamp(data_txt) + pd.Timedelta(milliseconds=inicio_ms) - pd.Timedelta(hours=3)
    percurso = total_s - janela_s
    if percurso <= 0:
        raise ValueError("janela_s must be smaller than total_s")
    n_frames = max(1, int(round(percurso * fps / velocidade)))
    passo = percurso / n_frames
    print(f"  {len(e):,} events | {n_frames} frames | window {janela_s:g}s "
          f"sliding {passo*1000:.1f} ms/frame at {fps} fps "
          f"({velocidade:g}x speed)")

    # ---- camera track: smooth y-range that follows the price ---------------
    grade = np.arange(n_frames) * passo
    lo, hi = [], []
    for g in grade:
        jan = e[(e["t"] >= g) & (e["t"] <= g + janela_s)]
        if jan.empty or jan["bid"].isna().all():
            lo.append(np.nan); hi.append(np.nan)
        else:
            lo.append(jan["bid"].min()); hi.append(jan["ask"].max())
    lo = pd.Series(lo).ffill().bfill()
    hi = pd.Series(hi).ffill().bfill()
    # rolling mean = camera damping; without it the view snaps every frame
    suav = max(3, int(fps * 0.6))
    lo = lo.rolling(suav, center=True, min_periods=1).mean()
    hi = hi.rolling(suav, center=True, min_periods=1).mean()
    folga = (hi - lo).clip(lower=5 * TICK_SIZE) * 0.22

    plt.style.use("dark_background")
    sns.set_context("paper", font_scale=1.0)
    dpi = 100
    alt = 0.62 if mostrar_volume else 0.50
    razoes = [3, 1, 1.1] if mostrar_volume else [3, 1]
    fig, axes = plt.subplots(
        len(razoes), 1, figsize=(largura_px / dpi, largura_px * alt / dpi), dpi=dpi,
        sharex=True, gridspec_kw={"height_ratios": razoes, "hspace": 0.12})
    a1, a2 = axes[0], axes[1]
    a3 = axes[2] if mostrar_volume else None
    fig.patch.set_facecolor("#111111")
    for a_ in axes:
        a_.set_facecolor("#111111")

    # everything is drawn ONCE; frames only move the viewport
    a1.fill_between(e["t"], e["bid"], e["ask"], step="post",
                    color="#2a3f5f", alpha=0.55, lw=0, rasterized=True)
    a1.step(e["t"], e["bid"], where="post", color="lime", lw=1.5, rasterized=True)
    a1.step(e["t"], e["ask"], where="post", color="red", lw=1.5, rasterized=True)
    # ambiguous first, underneath and hollow -- visible but clearly secondary
    if mostrar_ambiguos and len(amb):
        ab = amb[amb["side"] == 0]
        asl = amb[amb["side"] == 1]
        a1.scatter(ab["t"], ab["px"], s=90, facecolors="none", marker="^", zorder=5,
                   edgecolors="lime", linewidths=1.0, alpha=0.55)
        a1.scatter(asl["t"], asl["px"], s=90, facecolors="none", marker="v", zorder=5,
                   edgecolors="red", linewidths=1.0, alpha=0.55)
    a1.scatter(ucb["t"], ucb["px"], s=130, c="lime", marker="^", zorder=6,
               edgecolors="white", linewidths=0.7)
    a1.scatter(ucs["t"], ucs["px"], s=130, c="red", marker="v", zorder=6,
               edgecolors="white", linewidths=0.7)
    if mostrar_trades:
        a1.scatter(tb["t"], tb["px"], s=np.clip(tb["size"] * 5, 20, 320), c="cyan",
                   marker="X", zorder=8, edgecolors="white", linewidths=0.5, alpha=0.95)
        a1.scatter(ts_["t"], ts_["px"], s=np.clip(ts_["size"] * 5, 20, 320), c="magenta",
                   marker="X", zorder=8, edgecolors="white", linewidths=0.5, alpha=0.95)
    a1.set_ylabel("Price (points)", color="white")

    a2.step(e["t"], e["spread"], where="post", color="gold", lw=1.3)
    a2.fill_between(e["t"], 1, e["spread"], step="post", color="gold", alpha=0.25, lw=0)
    a2.axhline(1, color="white", ls=":", lw=1, alpha=0.6)
    a2.set_ylabel("Spread (ticks)", color="gold")
    # scale to a high percentile, not the max: one 14-tick spike would otherwise
    # flatten the 1-2 tick norm into an unreadable line
    sp_cap = max(4.0, float(np.nanpercentile(e["spread"].dropna(), 99.5)))
    a2.set_ylim(0.9, sp_cap * 1.15)
    a2.tick_params(axis="y", colors="gold")

    if mostrar_volume:
        larg = janela_s / 900.0
        a3.bar(tb["t"], tb["size"], width=larg, color="cyan", alpha=0.9)
        a3.bar(ts_["t"], -ts_["size"], width=larg, color="magenta", alpha=0.9)
        a3.axhline(0, color="white", lw=1, alpha=0.7)
        a3.set_ylabel("Traded qty\n(buy + / sell -)", color="white")
    axes[-1].set_xlabel("Market time (s)", color="white")
    if mostrar_volume:
        # big sweeps clip rather than erase the rest
        vmax = (max(10.0, float(np.percentile(trades["size"], 98)) * 1.6)
                if len(trades) else 1.0)
        a3.set_ylim(-vmax, vmax)

    for a_ in axes:
        a_.grid(True, color="gray", alpha=0.15, ls=":")
        a_.tick_params(colors="white")
        for sp in ("top", "right"):
            a_.spines[sp].set_visible(False)
        for sp in ("bottom", "left"):
            a_.spines[sp].set_color("white")

    legenda = [
        Line2D([], [], color="lime", lw=2, label="Best bid"),
        Line2D([], [], color="red", lw=2, label="Best ask"),
        Patch(facecolor="#2a3f5f", alpha=0.8, label="Spread"),
        Line2D([], [], color="lime", marker="^", ls="", ms=9, mec="white",
               label="Buy undercut (strict)"),
        Line2D([], [], color="red", marker="v", ls="", ms=9, mec="white",
               label="Sell undercut (strict)"),
    ]
    if mostrar_ambiguos:
        legenda.append(
            Line2D([], [], marker="^", ls="", ms=9, mfc="none", mec="white",
                   label="Ambiguous (trade-adjacent, may be aggressor residual)"))
    if mostrar_trades:
        legenda += [
            Line2D([], [], color="cyan", marker="X", ls="", ms=9, mec="white",
                   label="Aggressive buy"),
            Line2D([], [], color="magenta", marker="X", ls="", ms=9, mec="white",
                   label="Aggressive sell"),
        ]
    a1.legend(handles=legenda, facecolor="#111111", edgecolor="#111111",
              labelcolor="white", loc="lower left", bbox_to_anchor=(0, 1.16),
              fontsize=8, ncol=len(legenda), frameon=False)
    titulo = a1.set_title("", color="white", pad=34, fontsize=13, fontweight="bold")

    def frame(k):
        g = k * passo
        a1.set_xlim(g, g + janela_s)
        a1.set_ylim(lo.iloc[k] - folga.iloc[k], hi.iloc[k] + folga.iloc[k])
        agora = t0 + pd.Timedelta(seconds=g)
        jan = e[(e["t"] >= g) & (e["t"] <= g + janela_s)]
        nu = int(((jan["action"] == 0) & (jan["lbl"] == 1)).sum())
        na = int(((jan["action"] == 0) & (jan["lbl"] == 6)).sum())
        nt = int((jan["action"] == 9).sum())
        extra = f"   {nt} trades" if mostrar_trades else ""
        amb_txt = f"  (+{na} ambiguous)" if mostrar_ambiguos else ""
        titulo.set_text(
            f"{agora:%H:%M:%S.%f}"[:-3] + f" BRT   |   {janela_s:g}s window   "
            f"|   {nu} strict undercuts{amb_txt}{extra}")
        return ()

    # ---- pick the writer ---------------------------------------------------
    exe = _achar_ffmpeg()
    if formato == "auto":
        formato = "mp4" if exe else "gif"
    if formato == "mp4":
        if not exe:
            raise SystemExit(
                "formato='mp4' but no ffmpeg found. Install it with\n"
                "  sudo apt install ffmpeg\n"
                "or, without root, into this venv:\n"
                "  pip install imageio-ffmpeg")
        matplotlib.rcParams["animation.ffmpeg_path"] = exe
        # yuv420p + even dimensions = plays everywhere, including browsers
        escritor = animation.FFMpegWriter(
            fps=fps, bitrate=-1,
            extra_args=["-vcodec", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
                        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-movflags", "+faststart"])
        print(f"Rendering frames (mp4 via {exe})...")
    else:
        escritor = animation.PillowWriter(fps=fps)
        print("Rendering frames (gif)...")

    ani = animation.FuncAnimation(fig, frame, frames=n_frames, interval=1000 / fps)
    saida = saida or IMG_PANEL.with_name(
        IMG_PANEL.stem + f"_filme_{data_txt}_{inicio_ms}.{formato}")
    ani.save(saida, writer=escritor, savefig_kwargs={"facecolor": fig.get_facecolor()})
    plt.close(fig)

    mb = saida.stat().st_size / 1e6
    print(f"\n[SUCCESS] Movie saved: {saida}")
    print(f"  {n_frames} frames at {fps} fps = {n_frames/fps:.1f}s of playback "
          f"for {percurso:g}s of market time travelled ({velocidade:g}x) | {mb:.1f} MB")
    return saida


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None        # optional: day, e.g. "15"

    # single-day mode writes its own files so the full-month panel is never clobbered
    global ARQ_PANEL, ARQ_REPORT, IMG_PANEL
    if only:
        ARQ_PANEL = ARQ_PANEL.with_name(ARQ_PANEL.stem + f"_d{only}.parquet")
        ARQ_REPORT = ARQ_REPORT.with_name(ARQ_REPORT.stem + f"_d{only}.csv")
        IMG_PANEL = IMG_PANEL.with_name(IMG_PANEL.stem + f"_d{only}.png")

    if not ARQ_PANEL.exists() or only:
        day_dirs = sorted(p for p in BASE_INPUT_DIR.iterdir() if p.is_dir())
        if only:
            day_dirs = [p for p in day_dirs if p.name == only]
        print(f"Replaying the order book for {len(day_dirs)} day(s)... (this takes a while)")

        painels, reports = [], []
        if len(day_dirs) == 1:
            res = [processar_dia(day_dirs[0])]
        else:
            with ProcessPoolExecutor(max_workers=min(MAX_DAY_WORKERS, len(day_dirs))) as ex:
                res = list(ex.map(processar_dia, day_dirs))
        for r in res:
            if r is None:
                continue
            painels.append(r["minutes"])
            reports.append(r["report"])
            print(f"  {r['report']['date']}: real undercut {r['report']['undercut_real']:,} | "
                  f"no spread change {r['report']['no_spread_change_total']:,} | "
                  f"check delta {r['report']['narrowed_vs_undercut_delta']}", flush=True)

        if not painels:
            print("No data extracted.")
            return

        pl.concat(painels, how="diagonal").sort("window_key").write_parquet(ARQ_PANEL)
        rep = pl.DataFrame(reports).sort("date")
        rep.write_csv(ARQ_REPORT)
    else:
        rep = pl.read_csv(ARQ_REPORT)

    imprimir_relatorio(rep)
    gerar_dashboard()          # most recent day
    if rep.height > 1:
        gerar_visao_mensal()        # all days at a glance
        gerar_serie_continua("1h")  # continuous hour-by-hour, day-by-day series
        gerar_frequencia_spread()   # spread-length frequency across the clock


if __name__ == "__main__":
    main()
