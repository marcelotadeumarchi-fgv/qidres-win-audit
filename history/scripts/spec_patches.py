"""
SPEC PATCHES — implements the three accepted modifications.

Kept SEPARATE from spec_hipoteses.py so the original specification tests and the
patched tests never get mixed up in reporting.

  P1a  event-driven windows                     -> spec_evento.py (needs a replay)
  P1b  live-size FIFO queue                     -> ALREADY IMPLEMENTED in
                                                   undercutting_valida.py; verified
                                                   mean QP 0.4063/0.4011 over 5.36M
                                                   level-1 cancellations
  P2a  lead-lag with lagged return and OFI controls      -> this file
  P2b  placebo by PARTIAL R-SQUARED, not raw coefficients -> this file
  P3b  intra-asset constrained vs unconstrained regime    -> this file

WHY P2b CHANGED. The patch proposed rejecting when |gamma| >> |beta_fwd|, where
gamma comes from  FQCR ~ R(t-k)  and beta from  R(t+k) ~ FQCR. Those regressions
have different dependent variables, so their coefficients are not commensurable
and the inequality is undefined. Partial R-squared is scale-free and answers the
intended question directly: which direction explains more?

WHY P3b MATTERS. The cross-instrument contrast confounds tick-bindingness with
liquidity -- both instruments have tick 5.0 near price 147,000. Comparing
WITHIN one instrument between moments when the spread is 1 tick (price
improvement is ILLEGAL) and moments when it is wider (improvement is legal)
holds instrument, participants and tick fixed. The regimes are still endogenous
to volatility, so this is not exogenous variation, but it is a far better
identification of the tick constraint than the cross-instrument design.
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import polars as pl
import statsmodels.api as sm

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import spec_hipoteses as S          # noqa: E402

TICK = 5.0
P_LAGS = 5                           # lags for the lead-lag model
N_SPEC = []


# ---------------------------------------------------------------- helpers ---
def defasar(d: pl.DataFrame, col: str, j: int, nome: str) -> pl.DataFrame:
    """Lag a column by j rows WITHOUT crossing a day boundary."""
    return d.with_columns(**{
        nome: pl.when(pl.col("dia_").shift(j) == pl.col("dia_"))
        .then(pl.col(col).shift(j)).otherwise(None)})


def r2_parcial(d: pl.DataFrame, y: str, var: str, controles: list) -> float:
    """Partial R^2 of `var` after `controles`:  (R2_full - R2_rest)/(1 - R2_rest)."""
    cols = [var] + controles
    dd = d.drop_nulls([y] + cols)
    for c in cols:
        dd = dd.filter(pl.col(c).is_finite())
    if dd.height < 200:
        return float("nan")
    yv = dd[y].to_numpy()
    cheio = sm.OLS(yv, S._X(dd, cols)).fit()
    if controles:
        rest = sm.OLS(yv, S._X(dd, controles)).fit()
        r2r = rest.rsquared
    else:
        r2r = 0.0
    return (cheio.rsquared - r2r) / (1 - r2r) if r2r < 1 else float("nan")


# ------------------------------------------------------------------- P3b ----
def regime(d: pl.DataFrame) -> pl.DataFrame:
    """CONSTRAINED = every observation in the window had a 1-tick spread, so
    passive price improvement was not legally possible. UNCONSTRAINED = the
    spread exceeded one tick at some point."""
    return d.with_columns(
        constrito=(pl.col("spr") <= TICK + 1e-9),
        frac_largo=(pl.col("spr") - TICK) / TICK,     # mean ticks above the floor
    )


def p3b(paineis: dict):
    S.cab("P3b — INTRA-ASSET REGIME TEST  (constrained vs unconstrained, same instrument)")
    print("  CONSTRAINED  : spread was 1 tick for the whole window -> price")
    print("                 improvement is ILLEGAL, so the price channel is shut.")
    print("  UNCONSTRAINED: spread exceeded 1 tick -> improvement is legal.\n")
    for sid, (d, rot) in paineis.items():
        d = regime(d)
        nc = d.filter(pl.col("constrito")).height
        nu = d.height - nc
        print(f"  {rot}")
        print(f"    windows: constrained {nc:,} ({100*nc/d.height:.1f}%)  "
              f"unconstrained {nu:,} ({100*nu/d.height:.1f}%)")
        print(f"    {'regime':<16}{'h':>4}{'signal':<8}{'beta':>12}{'z':>9}"
              f"{'p':>11}{'partial R2':>12}{'n':>10}")
        for rot_r, sub in (("constrained", d.filter(pl.col("constrito"))),
                           ("unconstrained", d.filter(~pl.col("constrito")))):
            if sub.height < 500:
                print(f"    {rot_r:<16} too few windows ({sub.height})")
                continue
            for h in (5, 10):
                dd = S.alvo(sub, h)
                for sn in ("fqcr", "ur_price"):
                    r = S.reg(dd, [sn, "agg_cancel", "ofi", "agr_imb"],
                              f"P3b {sid} {rot_r} {h} {sn}")
                    pr = r2_parcial(dd, "ret", sn, ["agg_cancel", "ofi", "agr_imb"])
                    N_SPEC.append(f"P3b {sid} {rot_r} {h} {sn}")
                    print(f"    {rot_r:<16}{h:>3}s{sn:<8}{r['beta']:>12.4f}"
                          f"{r['z']:>9.2f}{r['p']:>11.5f}{100*pr:>11.4f}%"
                          f"{r['n']:>10,}  {S.sig(r['p'])}")
        print()


# ------------------------------------------------------------------- P2a ----
def p2a(paineis: dict, sinal: str = "fqcr"):
    S.cab(f"P2a — LEAD-LAG with lagged RETURN and OFI controls   (signal = {sinal})")
    print("  R(t+k) = a + SUM_j b_j*SIGNAL(t-j) + SUM_j g_j*R(t-j) + SUM_j d_j*OFI(t-j)")
    print(f"  P = {P_LAGS} lags. The forward capacity is the JOINT test on the")
    print("  SIGNAL block AFTER past returns and past order flow are controlled.\n")
    print(f"  {'regime':<16}{'k':>4}{'b_0':>11}{'z_0':>8}{'sum b_j':>11}"
          f"{'F(joint)':>11}{'p(joint)':>11}{'n':>10}")
    for sid, (d, rot) in paineis.items():
        for k in (5, 10):
            dd = d
            sig_cols, ret_cols, ofi_cols = [], [], []
            for j in range(0, P_LAGS + 1):
                dd = defasar(dd, sinal, j, f"s{j}") if j else dd.with_columns(s0=pl.col(sinal))
                sig_cols.append(f"s{j}")
            for j in range(1, P_LAGS + 1):
                dd = defasar(dd, "ret_1s", j, f"r{j}")
                dd = defasar(dd, "ofi", j, f"o{j}")
                ret_cols.append(f"r{j}"); ofi_cols.append(f"o{j}")
            dd = S.alvo(dd, k)
            cols = sig_cols + ret_cols + ofi_cols
            dd = dd.drop_nulls(["ret"] + cols)
            for c in cols:
                dd = dd.filter(pl.col(c).is_finite())
            if dd.height < 1000:
                print(f"  {rot[:14]:<16}{k:>3}s  too few rows"); continue
            X = S._X(dd, cols)
            f = sm.OLS(dd["ret"].to_numpy(), X).fit(cov_type="HAC",
                                                    cov_kwds={"maxlags": k + 2})
            R = np.zeros((len(sig_cols), X.shape[1]))
            for i in range(len(sig_cols)):
                R[i, 1 + i] = 1.0
            teste = f.f_test(R)
            soma = float(sum(f.params[1 + i] for i in range(len(sig_cols))))
            N_SPEC.append(f"P2a {sid} k={k}")
            print(f"  {rot[:14]:<16}{k:>3}s{f.params[1]:>11.4f}{f.tvalues[1]:>8.2f}"
                  f"{soma:>11.4f}{float(teste.fvalue):>11.3f}{float(teste.pvalue):>11.5f}"
                  f"{int(f.nobs):>10,}  {S.sig(float(teste.pvalue))}")


# ------------------------------------------------------------------- P2b ----
def p2b(paineis: dict, sinal: str = "fqcr"):
    S.cab(f"P2b — PLACEBO by PARTIAL R-SQUARED   (signal = {sinal})")
    print("  FORWARD : partial R2 of SIGNAL(t) explaining R(t+k)")
    print("  BACKWARD: partial R2 of R(t-k) explaining SIGNAL(t)")
    print("  Both controlled for aggregate cancellation and OFI, so the two are")
    print("  commensurable. Raw coefficients are NOT (different dependent vars).\n")
    print(f"  {'regime':<16}{'k':>4}{'fwd partial R2':>16}{'bwd partial R2':>16}"
          f"{'ratio bwd/fwd':>15}{'verdict':>12}")
    for sid, (d, rot) in paineis.items():
        for k in (5, 10):
            dd = S.alvo(d, k)
            fwd = r2_parcial(dd, "ret", sinal, ["agg_cancel", "ofi"])
            db = S.alvo(d, k, passado=True).rename({"ret": "ret_pas"})
            bwd = r2_parcial(db, sinal, "ret_pas", ["agg_cancel", "ofi"])
            raz = bwd / fwd if (fwd and np.isfinite(fwd) and fwd > 0) else np.nan
            vd = "REACTIVE" if (np.isfinite(raz) and raz > 1.0) else "ok"
            N_SPEC.append(f"P2b {sid} k={k}")
            print(f"  {rot[:14]:<16}{k:>3}s{100*fwd:>15.4f}%{100*bwd:>15.4f}%"
                  f"{raz:>15.2f}{vd:>12}")
    print("\n  verdict REACTIVE: the past return explains the signal better than the")
    print("  signal explains the future return -> reject as a reactive artifact.")


def main():
    paineis = {}
    for sid, rot in ((S.NARROW, "NARROW-SPREAD (WIN)"),
                     (S.WIDE, "WIDE-SPREAD (control)")):
        f = S.DIR / f"painel_{sid}.parquet"
        if not f.exists():
            print(f"  missing {f.name} — skipping")
            continue
        paineis[sid] = (S.carregar(sid), rot)
        print(f"{rot:<28} {paineis[sid][0].height:>9,} one-second rows, "
              f"{paineis[sid][0]['dia_'].n_unique()} days")
    if not paineis:
        raise SystemExit("no panels yet")

    p3b(paineis)
    for sn in ("fqcr", "qcr"):
        p2a(paineis, sn)
        p2b(paineis, sn)

    S.cab("PATCH RUN — SPECIFICATION COUNT")
    print(f"  {len(N_SPEC)} specifications estimated in this patch run.")
    print("  P1b (live-size FIFO) was already implemented and is not re-tested here.")
    print("  P1a (event windows) requires a separate replay — see spec_evento.py.")
    print("=" * 92)


if __name__ == "__main__":
    main()
