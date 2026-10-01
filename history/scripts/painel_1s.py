"""Build a 1-SECOND panel for all days, carrying the cancellation channels.

Motivated by the OBI validation: informational dynamics in this market live
between 100 ms and ~10 s. The 1-minute panel used so far is far too coarse to
see a defensive withdrawal, which is a burst, not a level.
"""
import os, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import polars as pl
sys.path.insert(0, str(Path(__file__).parent))
import undercutting_valida as uv

OUT = Path(__file__).parent / "output_data/painel1s"
OUT.mkdir(parents=True, exist_ok=True)

COLS = ["window_1m", "touch_wd_vol_bid", "touch_wd_vol_ask",
        "deep_wd_vol_bid", "deep_wd_vol_ask",
        "touch_add_vol_bid", "touch_add_vol_ask",
        "touch_cancel_n_bid", "touch_cancel_n_ask",
        "touch_life_bid", "touch_life_ask",
        "touch_depth_bid", "touch_depth_ask", "touch_depth_obs",
        "sweep_buy", "sweep_sell", "trade_count", "trade_volume",
        "uc_strict_vol_bid", "uc_strict_vol_ask",
        "new_vol_bid", "new_vol_ask",
        "close_microprice", "close_midprice",
        "spread_pts_sum", "spread_all_obs", "mid_price_sum", "mid_obs"]


def um_dia(p: Path):
    os.environ["POLARS_MAX_THREADS"] = "4"
    data = p.stem.split("_")[-1]
    dest = OUT / f"p1s_{data}.parquet"
    if dest.exists():
        return dest
    r = uv.replay_dia(p, bucket="1s")
    if r is None:
        return None
    r["minutes"].select([c for c in COLS if c in r["minutes"].columns]) \
        .with_columns(dia=pl.lit(data)).write_parquet(dest)
    return dest


if __name__ == "__main__":
    fs = sorted(uv.OUTPUT_DIR.glob(f"undercut_valid_events_{uv.TARGET_SECURITY_ID}_*.parquet"))
    print(f"building 1-second panels for {len(fs)} days...")
    with ProcessPoolExecutor(max_workers=6) as ex:
        ok = [x for x in ex.map(um_dia, fs) if x]
    df = pl.concat([pl.read_parquet(f) for f in ok], how="diagonal").sort("window_1m")
    df.write_parquet(OUT / "painel_1s_FULL.parquet")
    print(f"{len(ok)} days | {df.height:,} one-second rows -> painel_1s_FULL.parquet")
