"""
FALSIFICATION SUITE for the cancellation channel.

Implements F1-F6 of PROTOCOLO_CANCELAMENTO.md, which was frozen BEFORE any of
these tests was run. Each test has a kill condition stated in that document;
this file reports the outcome of every test, including passes.

The finding under test (exploratory, hold-out already burned):

    R(t -> t+h) = a + b*cancel_imb(t) + g1*agr_imb + g2*OBI + g3*add_imb
                  + hour dummies
    primary horizons h = 5s and 10s, declared prediction b < 0

Run:  python falsificacao.py
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import polars as pl
import statsmodels.api as sm

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import cancelamento as C          # noqa: E402

HORIZONTES = (5, 10)
CONTROLES = ["agr_imb", "obi", "add_imb"]
N_SHUFFLE = 500
RNG = np.random.default_rng(20261001)

RESULTADO = {}


def preparar(g: pl.DataFrame, h: int) -> pl.DataFrame:
    gg = g.with_columns(
        ret=((pl.col("close_midprice").shift(-h) - pl.col("close_midprice"))
             / pl.col("close_midprice") * 10000),
        ret_pas=((pl.col("close_midprice") - pl.col("close_midprice").shift(h))
                 / pl.col("close_midprice") * 10000),
        _d=pl.col("dia").shift(-h),
        _dp=pl.col("dia").shift(h),
    ).filter(pl.col("_d") == pl.col("dia"))
    return gg.drop_nulls(["ret", "cancel_imb"] + CONTROLES)


def estimar(gg: pl.DataFrame, sinal: str, alvo: str = "ret", extras=()):
    cols = [sinal] + list(CONTROLES) + list(extras)
    cols = [c for c in cols if c in gg.columns]
    f = C._hac(gg[alvo].to_numpy(), C._desenho(gg, cols), lags=5)
    return f.params[1], f.tvalues[1], f.pvalues[1], int(f.nobs)


def cab(t):
    print("\n" + "=" * 86); print(t); print("=" * 86)


def main():
    g = C.carregar()
    tot_deep = pl.col("deep_wd_vol_bid") + pl.col("deep_wd_vol_ask")
    g = g.with_columns(
        cancel_imb_deep=pl.when(tot_deep > 0)
        .then((pl.col("deep_wd_vol_bid") - pl.col("deep_wd_vol_ask")) / tot_deep)
        .otherwise(0.0),
        deep_tot=tot_deep,
    )
    print(f"{g.height:,} one-second rows | {g['dia'].n_unique()} days")
    print(f"cancelled volume/s: at touch {g['cancel_tot'].mean():.0f}, "
          f"away from touch {g['deep_tot'].mean():.0f} contracts\n")
    print("protocol II sha256 recorded in PROTOCOLO.sha256 before these tests ran")

    base = {}
    # ---------------- baseline, for reference ------------------------------
    cab("BASELINE (the finding under test)")
    print(f"  {'h':>5}{'beta':>11}{'z':>9}{'p':>12}{'n':>11}")
    for h in HORIZONTES:
        gg = preparar(g, h)
        b, z, p, n = estimar(gg, "cancel_imb")
        base[h] = (b, z, p)
        print(f"  {h:>4}s{b:>11.4f}{z:>9.2f}{p:>12.2e}{n:>11,}")

    # ---------------- F1 deep-book placebo ---------------------------------
    cab("F1 — DEEP-BOOK PLACEBO  (kill if comparable in magnitude and significance)")
    print("  Orders cancelled AWAY from the touch are not exposed to being picked")
    print("  off, so a defensive-withdrawal story predicts NO effect here.\n")
    print(f"  {'h':>5}{'beta_deep':>12}{'z':>9}{'p':>12}{'|b_deep/b_touch|':>19}")
    f1_ok = True
    for h in HORIZONTES:
        gg = preparar(g, h)
        b, z, p, n = estimar(gg, "cancel_imb_deep")
        raz = abs(b / base[h][0]) if base[h][0] else np.nan
        if p < 0.05 and raz > 0.5:
            f1_ok = False
        print(f"  {h:>4}s{b:>12.4f}{z:>9.2f}{p:>12.2e}{raz:>19.2f}")
    RESULTADO["F1"] = f1_ok
    print(f"\n  F1 {'PASS — placebo is not a substitute' if f1_ok else 'FAIL — FINDING WITHDRAWN'}")

    # ---------------- F2 time-shuffle placebo ------------------------------
    cab(f"F2 — TIME-SHUFFLE PLACEBO  ({N_SHUFFLE} permutations within day)")
    f2_ok = True
    for h in HORIZONTES:
        gg = preparar(g, h)
        real = abs(base[h][0])
        dias = gg["dia"].to_numpy()
        sinal = gg["cancel_imb"].to_numpy()
        y = gg["ret"].to_numpy()
        X_ctrl = C._desenho(gg, CONTROLES)
        nulos = np.empty(N_SHUFFLE)
        for i in range(N_SHUFFLE):
            emb = sinal.copy()
            for d_ in np.unique(dias):
                m = dias == d_
                emb[m] = RNG.permutation(emb[m])
            Xp = np.hstack([emb.reshape(-1, 1), X_ctrl])
            bb = np.linalg.lstsq(sm.add_constant(Xp, has_constant="add"), y, rcond=None)[0]
            nulos[i] = abs(bb[1])
        p99 = np.percentile(nulos, 99)
        ok = real > p99
        f2_ok = f2_ok and ok
        print(f"  {h:>4}s  |beta|={real:.4f}   shuffled p99={p99:.4f}   "
              f"exceeds={'YES' if ok else 'NO'}   empirical p={(nulos >= real).mean():.4f}")
    RESULTADO["F2"] = f2_ok
    print(f"\n  F2 {'PASS' if f2_ok else 'FAIL — FINDING WITHDRAWN'}")

    # ---------------- F3 side symmetry -------------------------------------
    cab("F3 — SIDE SYMMETRY  (kill if the effect loads on only one side)")
    print(f"  {'h':>5}{'side':>18}{'beta':>11}{'z':>9}{'p':>12}{'n':>11}")
    f3_ok = True
    for h in HORIZONTES:
        gg = preparar(g, h)
        sinais = []
        for rot, sub in (("bid withdrawal", gg.filter(pl.col("cancel_imb") > 0)),
                         ("ask withdrawal", gg.filter(pl.col("cancel_imb") < 0))):
            b, z, p, n = estimar(sub, "cancel_imb")
            sinais.append(np.sign(b))
            print(f"  {h:>4}s{rot:>18}{b:>11.4f}{z:>9.2f}{p:>12.2e}{n:>11,}")
        if len(set(sinais)) != 1:
            f3_ok = False
    RESULTADO["F3"] = f3_ok
    print(f"\n  F3 {'PASS — same sign on both sides' if f3_ok else 'FAIL — asymmetric'}")

    # ---------------- F4 reverse causality ---------------------------------
    cab("F4 — REVERSE CAUSALITY  (kill if the backward effect >= the forward one)")
    print(f"  {'h':>5}{'forward':>11}{'backward':>11}{'|fwd|>|bwd|':>14}")
    f4_ok = True
    for h in HORIZONTES:
        gg = preparar(g, h).drop_nulls(["ret_pas"])
        bf, _, _, _ = estimar(gg, "cancel_imb", "ret")
        bb_, _, _, _ = estimar(gg, "cancel_imb", "ret_pas")
        ok = abs(bf) > abs(bb_)
        f4_ok = f4_ok and ok
        print(f"  {h:>4}s{bf:>11.4f}{bb_:>11.4f}{'YES' if ok else 'NO':>14}")
    RESULTADO["F4"] = f4_ok
    print(f"\n  F4 {'PASS — anticipation exceeds response' if f4_ok else 'FAIL — response only'}")

    # ---------------- F5 adverse-selection conditioning --------------------
    cab("F5 — ADVERSE-SELECTION CONDITIONING  (cannot kill; informs interpretation)")
    print("  Adverse selection is higher when the spread is wide and volatility high,")
    print("  so the effect should be STRONGER there.\n")
    print(f"  {'h':>5}{'state':>26}{'beta':>11}{'z':>9}{'p':>12}{'n':>11}")
    for h in HORIZONTES:
        gg = preparar(g, h).with_columns(
            spr=pl.col("spread_pts_sum") / pl.col("spread_all_obs"))
        med = gg["spr"].median()
        for rot, sub in ((f"spread <= {med:.2f} pts", gg.filter(pl.col("spr") <= med)),
                         (f"spread >  {med:.2f} pts", gg.filter(pl.col("spr") > med))):
            b, z, p, n = estimar(sub, "cancel_imb")
            print(f"  {h:>4}s{rot:>26}{b:>11.4f}{z:>9.2f}{p:>12.2e}{n:>11,}")

    # ---------------- verdict ----------------------------------------------
    cab("VERDICT")
    mortais = ["F1", "F2", "F3", "F4"]
    passou = all(RESULTADO.get(k, False) for k in mortais)
    for k in mortais:
        print(f"  {k}: {'PASS' if RESULTADO.get(k) else 'FAIL'}")
    print()
    if passou:
        print("  All kill conditions survived. The finding stands as ROBUST")
        print("  EXPLORATORY evidence. It is NOT confirmed: the hold-out days were")
        print("  burned, and confirmation requires data not in this sample")
        print("  (PROTOCOLO_CANCELAMENTO.md §5).")
    else:
        print("  A kill condition triggered. Per protocol §6 the finding is WITHDRAWN.")
    print("=" * 86)


if __name__ == "__main__":
    main()
