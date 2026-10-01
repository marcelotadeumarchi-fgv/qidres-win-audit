"""
SPEC §3 — HYPOTHESIS TESTS
==========================

Implements H1, H2, H3 of the research specification, on the two regime buckets
built by fila_pipeline.py.

REGIME LABELS. The spec calls the buckets "Large-Tick" and "Small-Tick", but its
own criterion selects on SPREAD IN TICKS, not on tick size. Measured:

    200001274203  tick 5.0  spread = 1 tick 99.03% of day   mean 1.010 ticks
    200001287487  tick 5.0  spread = 1 tick  2.83% of day   mean 2.669 ticks

The tick is 5.0 in BOTH and both trade near 147,000, so the tick-to-price ratio
is identical (~3.4e-5). The contrast is LIQUIDITY, not tick size. Buckets are
therefore labelled NARROW-SPREAD and WIDE-SPREAD, and H1's causal attribution
("due to the constrained tick channel") is NOT identified by this design.

ADDITION TO THE SPEC. H2 is gated on a reverse-causality test that the spec does
not contain. It is included because an earlier cancellation-based finding in this
project passed four placebo tests and still proved to be response rather than
anticipation (backward coefficient 12-16x the forward one). FQCR is a
cancellation measure with identical exposure.
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import polars as pl
import statsmodels.api as sm

warnings.filterwarnings("ignore")

BASE = Path(__file__).parent
DIR = BASE / "output_data/spec_fila"
NARROW, WIDE = 200001274203, 200001287487
SESSAO_INI, SESSAO_FIM = 9.0, 18.0
HORIZONTES = (1, 5, 10, 30)          # SPEC 2.5, seconds
N_SPEC = []


def carregar(sid: int) -> pl.DataFrame:
    d = pl.read_parquet(DIR / f"painel_{sid}.parquet")
    d = d.with_columns(brt=pl.col("window_1m") - pl.duration(hours=3))
    d = d.with_columns(hh=pl.col("brt").dt.hour() + pl.col("brt").dt.minute() / 60.0)
    d = d.filter((pl.col("hh") >= SESSAO_INI) & (pl.col("hh") <= SESSAO_FIM))
    d = d.filter(pl.col("close_midprice").is_not_null()
                 & pl.col("close_midprice").is_not_nan()).sort("window_1m")

    wd = pl.col("touch_wd_vol_bid") + pl.col("touch_wd_vol_ask")
    nv = pl.col("new_vol_bid") + pl.col("new_vol_ask")
    sw = pl.col("sweep_buy") + pl.col("sweep_sell")
    add = pl.col("touch_add_vol_bid") + pl.col("touch_add_vol_ask")

    return d.with_columns(
        # SPEC 2.1 — price undercutting run intensity, signed
        ur_price=pl.when(nv > 0)
        .then((pl.col("ur_run_len_bid") - pl.col("ur_run_len_ask")) / nv * 1000)
        .otherwise(0.0),
        ur_len_total=pl.col("ur_run_len_bid") + pl.col("ur_run_len_ask"),
        # SPEC 2.3 — front-of-queue cancellation ratio, SIGNED by side
        fqcr=pl.when(wd > 0)
        .then((pl.col("fq_wd_bid") - pl.col("fq_wd_ask")) / wd)
        .otherwise(0.0),
        # back-of-queue comparison (spec H2)
        bqcr=pl.when(wd > 0)
        .then((pl.col("bq_wd_bid") - pl.col("bq_wd_ask")) / wd)
        .otherwise(0.0),
        # aggregate cancellation control (spec H2)
        agg_cancel=pl.when(wd > 0)
        .then((pl.col("touch_wd_vol_bid") - pl.col("touch_wd_vol_ask")) / wd)
        .otherwise(0.0),
        # SPEC 2.4 — queue cancellation run intensity, signed
        qcr=pl.when(wd > 0)
        .then((pl.col("qcr_len_bid") - pl.col("qcr_len_ask")) / wd * 100)
        .otherwise(0.0),
        qcr_n=pl.col("qcr_n_bid") + pl.col("qcr_n_ask"),
        # OFI control (spec H2): touch additions minus cancellations, signed
        ofi=pl.when((add + wd) > 0)
        .then(((pl.col("touch_add_vol_bid") - pl.col("touch_wd_vol_bid"))
               - (pl.col("touch_add_vol_ask") - pl.col("touch_wd_vol_ask"))) / (add + wd))
        .otherwise(0.0),
        agr_imb=pl.when(sw > 0)
        .then((pl.col("sweep_buy") - pl.col("sweep_sell")) / sw).otherwise(0.0),
        obi=pl.col("close_microprice") - pl.col("close_midprice"),
        spr=pl.col("spread_pts_sum") / pl.col("spread_all_obs"),
        hora=pl.col("brt").dt.hour(),
    ).with_columns(
        # one-step past return, day-aware, for the P2a lagged-return controls
        ret_1s=pl.when(pl.col("dia_").shift(1) == pl.col("dia_"))
        .then((pl.col("close_midprice") / pl.col("close_midprice").shift(1)).log() * 10000)
        .otherwise(None)
    )


def alvo(d: pl.DataFrame, h: int, passado: bool = False) -> pl.DataFrame:
    """SPEC 2.5 log mid return. `passado` gives the BACKWARD return, for the
    reverse-causality gate."""
    if passado:
        r = (pl.col("close_midprice") / pl.col("close_midprice").shift(h)).log() * 10000
        d = d.with_columns(ret=r, _d=pl.col("dia_").shift(h))
    else:
        r = (pl.col("close_midprice").shift(-h) / pl.col("close_midprice")).log() * 10000
        d = d.with_columns(ret=r, _d=pl.col("dia_").shift(-h))
    return d.filter((pl.col("_d") == pl.col("dia_")) & pl.col("ret").is_not_null()
                    & pl.col("ret").is_finite())


def _X(d: pl.DataFrame, cols, dummies=True):
    x = [d[c].to_numpy().reshape(-1, 1) for c in cols]
    if dummies:
        h = d["hora"].to_numpy()
        for hh in np.unique(h)[1:]:
            x.append((h == hh).astype(float).reshape(-1, 1))
    return sm.add_constant(np.hstack(x), has_constant="add")


def reg(d: pl.DataFrame, cols, rot="", lags=5):
    d = d.drop_nulls(["ret"] + list(cols))
    for c in cols:
        d = d.filter(pl.col(c).is_finite())
    f = sm.OLS(d["ret"].to_numpy(), _X(d, cols)).fit(
        cov_type="HAC", cov_kwds={"maxlags": lags})
    N_SPEC.append(rot)
    return {"beta": f.params[1], "z": f.tvalues[1], "p": f.pvalues[1],
            "r2": f.rsquared, "n": int(f.nobs), "fit": f}


def sig(p):
    return "**" if p < 0.01 else ("*" if p < 0.05 else "")


def cab(t):
    print("\n" + "=" * 92); print(t); print("=" * 92)


def main():
    paineis = {}
    for sid, rot in ((NARROW, "NARROW-SPREAD (WIN, 1 tick 99.0% of day)"),
                     (WIDE, "WIDE-SPREAD (control, mean 2.67 ticks)")):
        f = DIR / f"painel_{sid}.parquet"
        if not f.exists():
            print(f"missing {f.name}"); continue
        paineis[sid] = (carregar(sid), rot)
        d = paineis[sid][0]
        print(f"{rot:<46} {d.height:>9,} one-second rows, {d['dia_'].n_unique()} days")

    # ---------------- H1 ----------------------------------------------------
    cab("H1 — DECAY OF PRICE-BASED UNDERCUTTING ACROSS REGIMES  (SPEC §3.1)")
    print("  R(t+h) = a + b1*UR_price(t) + controls.  Spec expects b1 significant in the")
    print("  wide-spread regime and broken in the narrow-spread one.")
    print("  NOTE: tick = 5.0 in BOTH regimes, so this contrasts LIQUIDITY, not tick size.\n")
    print(f"  {'regime':<16}{'h':>4}{'beta':>12}{'z':>9}{'p':>11}{'R2':>9}"
          f"{'mean run len':>14}{'n':>10}")
    for sid, (d, rot) in paineis.items():
        rl = (d["ur_run_len_bid"].sum() + d["ur_run_len_ask"].sum())
        rn = (d["ur_run_n_bid"].sum() + d["ur_run_n_ask"].sum())
        for h in HORIZONTES:
            r = reg(alvo(d, h), ["ur_price", "agr_imb", "obi", "spr"], f"H1 {sid} h={h}")
            print(f"  {rot[:14]:<16}{h:>3}s{r['beta']:>12.3f}{r['z']:>9.2f}{r['p']:>11.5f}"
                  f"{100*r['r2']:>8.3f}%{rl/max(rn,1):>14.2f}{r['n']:>10,}  {sig(r['p'])}")

    # ---------------- H2 GATE: reverse causality ----------------------------
    cab("H2 GATE — REVERSE CAUSALITY (addition to the spec; see module docstring)")
    print("  If FQCR explains the PAST return better than the FUTURE one, it is a")
    print("  response, not anticipation, and H2 cannot be interpreted as informational.\n")
    print(f"  {'regime':<16}{'h':>4}{'beta_fwd':>11}{'beta_bwd':>11}{'|fwd|>|bwd|':>14}")
    passou = {}
    for sid, (d, rot) in paineis.items():
        ok_all = True
        for h in (5, 10):
            rf = reg(alvo(d, h), ["fqcr", "agg_cancel", "ofi"], f"gate fwd {sid} {h}")
            rb = reg(alvo(d, h, passado=True), ["fqcr", "agg_cancel", "ofi"],
                     f"gate bwd {sid} {h}")
            ok = abs(rf["beta"]) > abs(rb["beta"])
            ok_all = ok_all and ok
            print(f"  {rot[:14]:<16}{h:>3}s{rf['beta']:>11.4f}{rb['beta']:>11.4f}"
                  f"{'YES' if ok else 'NO':>14}")
        passou[sid] = ok_all
        print(f"  -> {rot[:14]}: gate {'PASS' if ok_all else 'FAIL — H2 is response, not anticipation'}")

    # ---------------- H2 ----------------------------------------------------
    cab("H2 — INFORMATIONAL CONTENT OF FRONT-OF-QUEUE CANCELLATIONS  (SPEC §3.2)")
    print("  R(t+h) = a + b1*FQCR + b2*AggregateCancel + b3*OFI.")
    print("  Spec expects FQCR to beat AggregateCancel in explanatory power.\n")
    for sid, (d, rot) in paineis.items():
        print(f"  {rot}")
        print(f"    {'h':>4}{'signal':<14}{'beta':>12}{'z':>9}{'p':>11}{'R2':>9}{'n':>11}")
        for h in HORIZONTES:
            dd = alvo(d, h)
            for nome, cols in (("FQCR", ["fqcr", "agg_cancel", "ofi"]),
                               ("BQCR(back)", ["bqcr", "agg_cancel", "ofi"]),
                               ("AggCancel", ["agg_cancel", "ofi"])):
                r = reg(dd, cols, f"H2 {sid} {h} {nome}")
                print(f"    {h:>3}s{nome:<14}{r['beta']:>12.4f}{r['z']:>9.2f}"
                      f"{r['p']:>11.5f}{100*r['r2']:>8.3f}%{r['n']:>11,}  {sig(r['p'])}")
        print()

    # ---------------- H3 ----------------------------------------------------
    cab("H3 — QUEUE CANCELLATION RUNS AS PREDICTORS  (SPEC §3.3)")
    print("  Logit: P(mid moves over h) ~ QCR intensity + controls.")
    print("  Plus drift / spread-widening / volatility after QCR vs a baseline.\n")
    for sid, (d, rot) in paineis.items():
        print(f"  {rot}")
        print(f"    {'h':>4}{'logit b_QCR':>13}{'z':>9}{'p':>11}"
              f"{'drift QCR':>11}{'drift base':>12}{'n':>11}")
        for h in HORIZONTES:
            dd = alvo(d, h).drop_nulls(["qcr", "ofi", "agr_imb"])
            y = (dd["ret"].to_numpy() != 0).astype(float)
            X = _X(dd, ["qcr", "ofi", "agr_imb"])
            try:
                lg = sm.Logit(y, X).fit(disp=0)
                b, z, p = lg.params[1], lg.tvalues[1], lg.pvalues[1]
            except Exception:
                b = z = p = np.nan
            N_SPEC.append(f"H3 {sid} {h}")
            alto = dd.filter(pl.col("qcr").abs() > dd["qcr"].abs().quantile(0.90))
            base = dd.filter(pl.col("qcr") == 0)
            dq = (alto["ret"].to_numpy() * np.sign(alto["qcr"].to_numpy())).mean() if alto.height else np.nan
            db = base["ret"].abs().mean() if base.height else np.nan
            print(f"    {h:>3}s{b:>13.4f}{z:>9.2f}{p:>11.5f}{dq:>11.4f}{db:>12.4f}"
                  f"{len(y):>11,}  {sig(p) if np.isfinite(p) else ''}")
        print()

    cab("SPECIFICATION COUNT")
    print(f"  {len(N_SPEC)} specifications estimated in this run.")
    print("  H1 causal attribution NOT identified (tick constant across buckets).")
    print("  SPEC §4 tick-size deciles and §5 tick-to-price plot NOT executable.")
    print("=" * 92)


if __name__ == "__main__":
    main()
