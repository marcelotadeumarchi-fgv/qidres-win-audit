"""
SPEC PIPELINE — builds the panels for both regimes.

LARGE-TICK  : 200001274203 (WIN)          spread = 1 tick 99.03% of the day
WIDE-SPREAD : 200001287487 (control)      mean spread 2.669 ticks  [spec's
              "small-tick" bucket by its own criterion; the tick is 5.0 in BOTH,
              so this is a LIQUIDITY contrast, not a tick-size contrast]

Produces one 1-second panel per instrument carrying every SPEC §2 feature.
"""
import os, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import polars as pl
sys.path.insert(0, str(Path(__file__).parent))
import undercutting_valida as uv

GRANDE = 200001274203
CONTROLE = 200001287487
OUT = Path(__file__).parent / "output_data/spec_fila"
OUT.mkdir(parents=True, exist_ok=True)

COLS = ["window_1m", "dia_",
        # SPEC 2.1
        "ur_run_n_bid", "ur_run_n_ask", "ur_run_len_bid", "ur_run_len_ask",
        "uc_strict_vol_bid", "uc_strict_vol_ask",
        # SPEC 2.2/2.3
        "fq_wd_bid", "fq_wd_ask", "bq_wd_bid", "bq_wd_ask",
        "qp_sum_bid", "qp_sum_ask", "qp_n_bid", "qp_n_ask",
        "touch_wd_vol_bid", "touch_wd_vol_ask",
        "touch_add_vol_bid", "touch_add_vol_ask",
        # SPEC 2.4
        "qcr_n_bid", "qcr_n_ask", "qcr_len_bid", "qcr_len_ask",
        # controls / target
        "new_vol_bid", "new_vol_ask", "msg_total",
        "sweep_buy", "sweep_sell", "trade_count", "trade_volume",
        "touch_depth_bid", "touch_depth_ask", "touch_depth_obs",
        "spread_pts_sum", "spread_all_obs", "mid_price_sum", "mid_obs",
        "close_microprice", "close_midprice"]


def um_dia(arg):
    sid, day_dir = arg
    os.environ["POLARS_MAX_THREADS"] = "4"
    data = f"{day_dir.parent.parent.name}{day_dir.parent.name}{day_dir.name}"
    dest = OUT / f"spec_{sid}_{data}.parquet"
    if dest.exists():
        return dest
    ev = uv.extrair_eventos_dia(day_dir, sid=sid)
    if ev is None:
        return None
    r = uv.replay_dia(ev, bucket="1s")
    if r is None:
        return None
    m = r["minutes"].with_columns(dia_=pl.lit(data))
    m.select([c for c in COLS if c in m.columns]).write_parquet(dest)
    return dest


if __name__ == "__main__":
    # spawn, not fork. The parent touches polars between the two pools, and
    # forking after a rayon thread pool exists leaves the children deadlocked
    # in futex_do_wait -- which is exactly what happened on the first attempt.
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)

    dias = sorted(p for p in uv.BASE_INPUT_DIR.iterdir() if p.is_dir())
    alvos = ((GRANDE, "LARGE-TICK (WIN)"), (CONTROLE, "WIDE-SPREAD (control)"))
    if len(sys.argv) > 1:                      # one instrument per invocation
        esc = int(sys.argv[1])
        alvos = tuple(a for a in alvos if a[0] == esc)
    for sid, rot in alvos:
        print(f"\n=== {rot}  sid={sid} ===", flush=True)
        tarefas = [(sid, d) for d in dias]
        with ProcessPoolExecutor(max_workers=6) as ex:
            ok = [x for x in ex.map(um_dia, tarefas) if x]
        if not ok:
            print("  no data"); continue
        df = pl.concat([pl.read_parquet(f) for f in ok], how="diagonal").sort("window_1m")
        df.write_parquet(OUT / f"painel_{sid}.parquet")
        print(f"  {len(ok)} days | {df.height:,} one-second rows", flush=True)
