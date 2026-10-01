"""
CANCELLATION AS THE LIQUIDITY PROVIDER'S REACTION CHANNEL
=========================================================

Barardehi, Dixon & Liu (2026) model informed risk as causing liquidity providers
to WITHDRAW from competition. Withdrawal is a RESPONSE to informed flow, not a
forecast of it. Earlier tests in this project asked the forecasting question and
found nothing; this file asks the response question, which is the mechanism as
the reference paper actually states it.

Three measurement choices are deliberately different from the earlier null:

  1. RATE, not level.  A defensive withdrawal is a burst of cancellations, not a
     change in average order lifetime.
  2. GROSS, not net.  `net_flow = added - cancelled` lets routine replenishment
     cancel out exactly the defensive withdrawal we are looking for.
  3. SECONDS, not minutes.  The OBI validation showed this market's
     informational dynamics live between 100 ms and ~10 s.

TWO-STAGE DESIGN

  Stage A (REACTION).   Does aggressive flow at t cause cancellation at t+k?
        cancel_ask(t+k) ~ aggressive_buy(t)        and the mirror
     An informed buyer sweeps the ask; the remaining ask orders are the ones
     exposed to being picked off, so THAT side should withdraw.

  Stage B (INFORMATION). Does the withdrawal itself carry information beyond
     the flow that triggered it?
        R(t+k -> t+k+h) ~ cancel_imbalance(t+k) + aggressive_flow(t) + controls
     If cancellation is purely mechanical replenishment, its coefficient dies
     once the triggering flow is controlled for. If liquidity providers know
     something, it survives.

Stage B is the test that matters. Stage A alone would only show that the book
reacts to being hit, which is not news.

EXPLORATORY. This is not covered by PROTOCOLO_PRE_ESPECIFICACAO.md. It is
reported as exploratory and the specification count is disclosed.
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import polars as pl
import statsmodels.api as sm

warnings.filterwarnings("ignore")

BASE = Path(__file__).parent
PAINEL = BASE / "output_data/painel1s/painel_1s_FULL.parquet"
SESSAO_INI, SESSAO_FIM = 9.0, 18.0


def carregar() -> pl.DataFrame:
    d = pl.read_parquet(PAINEL)
    d = d.with_columns(brt=pl.col("window_1m") - pl.duration(hours=3))
    d = d.with_columns(h=pl.col("brt").dt.hour() + pl.col("brt").dt.minute() / 60.0)
    d = d.filter((pl.col("h") >= SESSAO_INI) & (pl.col("h") <= SESSAO_FIM))
    d = d.filter(pl.col("close_midprice").is_not_null()
                 & pl.col("close_midprice").is_not_nan())
    d = d.sort("window_1m")

    tot_wd = pl.col("touch_wd_vol_bid") + pl.col("touch_wd_vol_ask")
    tot_sw = pl.col("sweep_buy") + pl.col("sweep_sell")
    return d.with_columns(
        # GROSS cancellation imbalance at the touch (rate, signed)
        cancel_imb=pl.when(tot_wd > 0)
        .then((pl.col("touch_wd_vol_bid") - pl.col("touch_wd_vol_ask")) / tot_wd)
        .otherwise(0.0),
        cancel_tot=tot_wd,
        # aggressive flow direction (sweep counts by inferred aggressor side)
        agr_imb=pl.when(tot_sw > 0)
        .then((pl.col("sweep_buy") - pl.col("sweep_sell")) / tot_sw)
        .otherwise(0.0),
        agr_tot=tot_sw,
        add_imb=pl.when((pl.col("touch_add_vol_bid") + pl.col("touch_add_vol_ask")) > 0)
        .then((pl.col("touch_add_vol_bid") - pl.col("touch_add_vol_ask"))
              / (pl.col("touch_add_vol_bid") + pl.col("touch_add_vol_ask")))
        .otherwise(0.0),
        obi=pl.col("close_microprice") - pl.col("close_midprice"),
        hora=pl.col("brt").dt.hour(),
    )


def _hac(y, X, lags=5):
    return sm.OLS(y, sm.add_constant(X, has_constant="add")).fit(
        cov_type="HAC", cov_kwds={"maxlags": lags})


def _desenho(g, cols):
    x = [g[c].to_numpy().reshape(-1, 1) for c in cols]
    hr = g["hora"].to_numpy()
    for hh in np.unique(hr)[1:]:
        x.append((hr == hh).astype(float).reshape(-1, 1))
    return np.hstack(x)


def futuro(g: pl.DataFrame, col: str, k: int, nome: str) -> pl.DataFrame:
    """Shift a column k seconds forward WITHOUT crossing a day boundary."""
    return g.with_columns(
        **{nome: pl.col(col).shift(-k), "_d": pl.col("dia").shift(-k)}
    ).filter(pl.col("_d") == pl.col("dia")).drop("_d")


N_SPEC = []


def main():
    g = carregar()
    print(f"{g.height:,} one-second rows | {g['dia'].n_unique()} days\n")
    print("DESCRIPTIVES (per second, session)")
    print(f"  cancelled volume at touch : {g['cancel_tot'].mean():8.1f} contracts")
    print(f"  aggressive orders         : {g['agr_tot'].mean():8.2f}")
    print(f"  cancel imbalance          : mean {g['cancel_imb'].mean():+.5f}  sd {g['cancel_imb'].std():.4f}")
    print(f"  aggressor imbalance       : mean {g['agr_imb'].mean():+.5f}  sd {g['agr_imb'].std():.4f}\n")

    # ---------------- STAGE A: does the book REACT? -------------------------
    print("=" * 88)
    print("STAGE A — REACTION: does aggressive flow cause cancellation on the exposed side?")
    print("  An informed BUYER sweeps the ask; remaining ASK orders are exposed.")
    print("  So agr_imb > 0 (buy pressure) should drive cancel_imb < 0 (ask withdrawal).")
    print("=" * 88)
    print(f"  {'lag':>6}{'beta':>12}{'z':>9}{'p':>12}{'R2':>9}{'n':>11}")
    for k in (1, 2, 3, 5, 10):
        gg = futuro(g, "cancel_imb", k, "y").drop_nulls(["y", "agr_imb"])
        f = _hac(gg["y"].to_numpy(), _desenho(gg, ["agr_imb"]))
        N_SPEC.append(f"A k={k}")
        mk = "**" if f.pvalues[1] < 0.01 else ("*" if f.pvalues[1] < 0.05 else "")
        print(f"  {k:>4}s{f.params[1]:>12.4f}{f.tvalues[1]:>9.2f}{f.pvalues[1]:>12.6f}"
              f"{100*f.rsquared:>8.3f}%{int(f.nobs):>11,}  {mk}")

    # ---------------- STAGE B: does the withdrawal INFORM? ------------------
    print("\n" + "=" * 88)
    print("STAGE B — INFORMATION: does cancellation predict price beyond the flow that")
    print("  triggered it?  Target = forward MIDPOINT change (bp), horizon h seconds.")
    print("=" * 88)
    for h in (1, 5, 10, 30):
        gg = g.with_columns(
            ret=((pl.col("close_midprice").shift(-h) - pl.col("close_midprice"))
                 / pl.col("close_midprice") * 10000),
            _d=pl.col("dia").shift(-h),
        ).filter(pl.col("_d") == pl.col("dia")).drop_nulls(["ret", "cancel_imb", "agr_imb", "obi"])
        y = gg["ret"].to_numpy()
        print(f"\n  horizon {h}s   (n={len(y):,})")
        print(f"    {'specification':<42}{'beta_cancel':>13}{'z':>9}{'p':>12}{'R2':>9}")
        for rot, cols in (
            ("cancel_imb alone", ["cancel_imb"]),
            ("+ aggressive flow", ["cancel_imb", "agr_imb"]),
            ("+ flow + OBI", ["cancel_imb", "agr_imb", "obi"]),
            ("+ flow + OBI + additions", ["cancel_imb", "agr_imb", "obi", "add_imb"]),
        ):
            f = _hac(y, _desenho(gg, cols))
            N_SPEC.append(f"B h={h} {rot}")
            mk = "**" if f.pvalues[1] < 0.01 else ("*" if f.pvalues[1] < 0.05 else "")
            print(f"    {rot:<42}{f.params[1]:>13.4f}{f.tvalues[1]:>9.2f}"
                  f"{f.pvalues[1]:>12.6f}{100*f.rsquared:>8.3f}%  {mk}")

    print("\n" + "=" * 88)
    print(f"EXPLORATORY — not covered by the protocol. {len(N_SPEC)} specifications estimated.")
    print("The test that matters is the LAST row of each Stage B block: if the")
    print("cancellation coefficient dies once flow and OBI are controlled for, the")
    print("withdrawal is mechanical replenishment, not information.")
    print("=" * 88)


if __name__ == "__main__":
    main()
