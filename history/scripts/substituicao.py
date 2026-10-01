"""
TICK-INDUCED SUBSTITUTION TEST
==============================

Barardehi, Dixon & Liu (2026) measure informed-trading risk through the PRICE
dimension of liquidity competition: how aggressively passive orders improve the
quote. This project has shown that on WIN that channel is mechanically closed —
97.0% of strict undercuts face a 2-tick spread, where the only legal improvement
is exactly one tick. A liquidity provider who wants to signal has nowhere to
move the price to.

HYPOTHESIS (substitution). When the tick forecloses price competition,
information migrates to the two dimensions that remain open:

    QUANTITY — how much depth sits at, is added to, or is pulled from the touch
    TIMING   — how fast touch liquidity is cancelled

TEST. Build one signed signal per dimension on identical 1-minute windows, and
run each through the SAME specification already fixed in
PROTOCOLO_PRE_ESPECIFICACAO.md §4:

    R(t->t+1min) = a + b*SIGNAL(t) + g*S(t) + hour dummies      HAC(2)

with a close-to-close microprice return, so no signal window overlaps its own
return window.

    PRICE    qid_signed  = (undercut volume bid - ask) / passive volume
    QUANTITY depth_imb   = (depth_bid - depth_ask) / (depth_bid + depth_ask)
             net_flow    = (added - cancelled) at the touch, bid minus ask
    TIMING   life_imb    = (mean lifetime bid - ask) / (bid + ask)

SIGN CONVENTIONS, declared before estimation:
    depth_imb > 0 : more depth on the bid  -> expected bullish
    net_flow  > 0 : net liquidity ADDED to the bid -> expected bullish
    life_imb  > 0 : bid orders live LONGER (more committed) -> expected bullish

HONESTY NOTE. Order-book imbalance predicting short-horizon returns is a long
established result and is NOT a contribution of this study. What would be new
here is the CONTRAST: the price channel null while the quantity/timing channels
are informative, in a market where the tick is known to foreclose the former.
A finding that all three are null is equally reportable.
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import polars as pl
import statsmodels.api as sm

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import protocolo_estimar as P          # noqa: E402

HORIZONTES = (1, 5, 15)


def construir(incluir_holdout: bool = True) -> pl.DataFrame:
    """1-minute windows carrying all three channels."""
    d = P.carregar(incluir_holdout)
    d = d.with_columns(w=pl.col("brt").dt.truncate("1m"))
    g = d.group_by("w").agg([
        # PRICE
        pl.col("uc_strict_vol_bid").sum(), pl.col("uc_strict_vol_ask").sum(),
        pl.col("new_vol_bid").sum(), pl.col("new_vol_ask").sum(),
        # QUANTITY
        pl.col("touch_depth_bid").sum(), pl.col("touch_depth_ask").sum(),
        pl.col("touch_depth_obs").sum(),
        pl.col("touch_add_vol_bid").sum(), pl.col("touch_add_vol_ask").sum(),
        pl.col("touch_wd_vol_bid").sum(), pl.col("touch_wd_vol_ask").sum(),
        # TIMING
        pl.col("touch_cancel_n_bid").sum(), pl.col("touch_cancel_n_ask").sum(),
        pl.col("touch_life_bid").sum(), pl.col("touch_life_ask").sum(),
        # controls and target
        pl.col("spread_pts_sum").sum(), pl.col("spread_all_obs").sum(),
        pl.col("mid_price_sum").sum(), pl.col("mid_obs").sum(),
        pl.col("close_microprice").drop_nans().last().alias("mp"),
        pl.col("dia").first(),
    ]).sort("w")

    g = g.filter((pl.col("mid_obs") > 0) & (pl.col("spread_all_obs") > 0)
                 & (pl.col("touch_depth_obs") > 0)
                 & (pl.col("touch_cancel_n_bid") > 0) & (pl.col("touch_cancel_n_ask") > 0)
                 & pl.col("mp").is_not_null() & pl.col("mp").is_not_nan())

    g = g.with_columns(
        new_vol=pl.col("new_vol_bid") + pl.col("new_vol_ask"),
        db=pl.col("touch_depth_bid") / pl.col("touch_depth_obs"),
        da=pl.col("touch_depth_ask") / pl.col("touch_depth_obs"),
        lb=pl.col("touch_life_bid") / pl.col("touch_cancel_n_bid"),
        la=pl.col("touch_life_ask") / pl.col("touch_cancel_n_ask"),
        nfb=pl.col("touch_add_vol_bid") - pl.col("touch_wd_vol_bid"),
        nfa=pl.col("touch_add_vol_ask") - pl.col("touch_wd_vol_ask"),
        avg_spread_pts=pl.col("spread_pts_sum") / pl.col("spread_all_obs"),
        avg_mid=pl.col("mid_price_sum") / pl.col("mid_obs"),
        hora=pl.col("w").dt.hour(),
    ).with_columns(
        # PRICE channel — the protocol's primary signal
        qid_signed=(pl.col("uc_strict_vol_bid") - pl.col("uc_strict_vol_ask")) / pl.col("new_vol"),
        # QUANTITY channel
        depth_imb=(pl.col("db") - pl.col("da")) / (pl.col("db") + pl.col("da")),
        net_flow=(pl.col("nfb") - pl.col("nfa"))
        / (pl.col("touch_add_vol_bid") + pl.col("touch_add_vol_ask")
           + pl.col("touch_wd_vol_bid") + pl.col("touch_wd_vol_ask")),
        # TIMING channel
        life_imb=(pl.col("lb") - pl.col("la")) / (pl.col("lb") + pl.col("la")),
        S=pl.col("avg_spread_pts") / pl.col("avg_mid"),
    )
    return g.filter(pl.col("new_vol") > 0)


def testar(g: pl.DataFrame, sinal: str, h: int):
    gg = g.with_columns(
        ret=((pl.col("mp").shift(-h) - pl.col("mp")) / pl.col("mp") * 10000),
        dia_fim=pl.col("dia").shift(-h),
    ).filter((pl.col("dia_fim") == pl.col("dia")) & pl.col("ret").is_not_null()
             & pl.col(sinal).is_finite())
    x = [gg[sinal].to_numpy().reshape(-1, 1), gg["S"].to_numpy().reshape(-1, 1)]
    hr = gg["hora"].to_numpy()
    for hh in np.unique(hr)[1:]:
        x.append((hr == hh).astype(float).reshape(-1, 1))
    X = sm.add_constant(np.hstack(x), has_constant="add")
    f = sm.OLS(gg["ret"].to_numpy(), X).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
    sd = gg[sinal].std()
    return {"beta": f.params[1], "z": f.tvalues[1], "p": f.pvalues[1],
            "r2": f.rsquared, "n": int(f.nobs),
            "efeito_pts": f.params[1] * sd / 10000 * gg["mp"].mean()}


CANAIS = [
    ("PRICE", "qid_signed", "undercut volume imbalance", "+"),
    ("QUANTITY", "depth_imb", "depth imbalance at the touch", "+"),
    ("QUANTITY", "net_flow", "net liquidity added at the touch", "+"),
    ("TIMING", "life_imb", "touch-order lifetime imbalance", "+"),
]


def main():
    g = construir(incluir_holdout=True)
    print(f"{g.height:,} one-minute windows | {g['dia'].n_unique()} days\n")

    print("DESCRIPTIVES")
    print(f"  mean depth at touch     : bid {g['db'].mean():7.1f}  ask {g['da'].mean():7.1f} contracts")
    print(f"  mean touch-order life   : bid {g['lb'].mean()/1000:7.1f}s ask {g['la'].mean()/1000:7.1f}s")
    for _, c, rot, _ in CANAIS:
        v = g[c].to_numpy()
        v = v[np.isfinite(v)]
        print(f"  {rot:<34} mean {v.mean():+.5f}  sd {v.std():.5f}")
    print()

    print("=" * 94)
    print("THREE CHANNELS, IDENTICAL SPECIFICATION (protocol §4), microprice close-to-close")
    print("=" * 94)
    for h in HORIZONTES:
        print(f"\n  horizon = {h} min")
        print(f"    {'channel':<10}{'signal':<13}{'beta':>13}{'z':>9}{'p':>11}"
              f"{'R2':>9}{'1sd effect':>13}")
        print("    " + "-" * 78)
        for canal, c, rot, _ in CANAIS:
            r = testar(g, c, h)
            marca = "**" if r["p"] < 0.01 else ("*" if r["p"] < 0.05 else "")
            print(f"    {canal:<10}{c:<13}{r['beta']:>13.2f}{r['z']:>9.3f}"
                  f"{r['p']:>11.6f}{100*r['r2']:>8.3f}%{r['efeito_pts']:>11.3f}pt  {marca}")
        print("    " + "-" * 78)

    print("\n  1sd effect = index points moved by a one-standard-deviation signal,")
    print("               against a 5.0 point (one tick) crossing cost.")
    print("  Order-book imbalance predicting short-horizon returns is a long")
    print("  established result; the contribution, if any, is the CONTRAST between")
    print("  the foreclosed price channel and the open quantity/timing channels.")
    print("=" * 94)


if __name__ == "__main__":
    main()
