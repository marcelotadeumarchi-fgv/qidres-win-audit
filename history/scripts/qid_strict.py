"""
QID_res ON THE STRICT-UNDERCUT SUBSET
=====================================
Replicates the thesis pipeline (Appendices A-C, and the final signed
specification of section 7) using the VALIDATED strict undercut count as the
numerator, instead of the preliminary forward-fill classification.

What is reproduced from the thesis, unchanged
---------------------------------------------
  * 5-minute aggregation windows
  * qid_ratio        = undercutting count / message count          (Appendix A)
  * spread_relativo  = avg_spread_pts / avg_midpoint               (Appendix A)
  * large-tick filter: keep only windows with avg_spread_pts > TICK_SIZE
  * DAILY OLS orthogonalisation  qid = a + b * spread_relativo,
    qid_res = residual                                             (eq. 2)
  * in-sample regression of forward midpoint return on qid_res,
    Newey-West HAC errors, horizons t+1 (5 min) and t+3 (15 min)   (Appendix B)
  * walk-forward expanding-window OOS + Clark-West test            (Appendix C)
  * signed measure QID_signed = undercut_bid - undercut_ask        (eq. 3)
  * |R_{t+h}| regression with hour dummies and 1%/99% winsorising  (eq. 4)

What differs, and why
---------------------
  * NUMERATOR. The thesis counts any new order improving the last seen price on
    its side, from a forward-filled top of book that never applies deletes. On
    this MBO feed that over-counts by ~22x (see undercutting_valida.py). Here the
    numerator is the book-verified STRICT undercut: an order that was
    non-marketable on arrival, demonstrably narrowed the spread, and did not
    arrive within 3 events of a same-side trade (so it is not possibly the
    residual of a partly-filled aggressive order).
  * STATE. The book is replayed with full depth, persistent across the session,
    with deletes and modifications applied -- the three corrections the thesis
    lists in section 6.3 as pending.
  * Both denominators are produced: `qid_msg` over all messages (what the
    Appendix A code does) and `qid_passive` over new passive orders (what
    equation (1) says). They are reported side by side.

Input : output_data/microstructure/undercut_valid_1m_<sid>.parquet  (1-min panel)
Output: output_data/qid_strict/qid_res_5m_strict_<sid>.parquet + console tables
"""

import warnings
from pathlib import Path

import numpy as np
import polars as pl
import statsmodels.api as sm

warnings.filterwarnings("ignore")

TARGET_SECURITY_ID = 200001274203
TICK_SIZE = 5.0
BASE = Path("/home/marchi/financial_ai_project/code/b3_data/qid")
PAINEL_1M = BASE / f"output_data/microstructure/undercut_valid_1m_{TARGET_SECURITY_ID}.parquet"
OUT_DIR = BASE / "output_data/qid_strict"
OUT_DIR.mkdir(parents=True, exist_ok=True)
ARQ_5M = OUT_DIR / f"qid_res_5m_strict_{TARGET_SECURITY_ID}.parquet"

HORA_INI, HORA_FIM = 9.0, 18.0      # BRT regular session
WARMUP = 500                        # thesis section 7 final spec


# ==============================================================================
# 1. 5-MINUTE PANEL
# ==============================================================================
def construir_painel_5m() -> pl.DataFrame:
    """Aggregate the validated 1-min panel into the thesis 5-min windows."""
    d = pl.read_parquet(PAINEL_1M)

    # strict undercut = verified spread-narrowing, minus the trade-adjacent
    # same-side cases that may be aggressor residuals
    d = d.with_columns(
        uc_strict_bid=pl.col("undercut_buy_count") - pl.col("undercut_resid_buy"),
        uc_strict_ask=pl.col("undercut_sell_count") - pl.col("undercut_resid_sell"),
        brt=pl.col("window_1m") - pl.duration(hours=3),
    ).with_columns(
        hhmm=pl.col("brt").dt.hour() + pl.col("brt").dt.minute() / 60.0
    ).filter((pl.col("hhmm") >= HORA_INI) & (pl.col("hhmm") <= HORA_FIM))

    d = d.with_columns(window_5m=pl.col("brt").dt.truncate("5m"))

    g = d.group_by("window_5m").agg([
        pl.col("uc_strict_bid").sum(),
        pl.col("uc_strict_ask").sum(),
        (pl.col("undercut_buy_count") + pl.col("undercut_sell_count")).sum().alias("uc_all"),
        pl.col("undercut_buy_count").sum().alias("uc_all_bid"),
        pl.col("undercut_sell_count").sum().alias("uc_all_ask"),
        # the thesis's own forward-fill heuristic, replayed on the same stream
        pl.col("naive_undercut_buy").sum().alias("uc_naive_bid"),
        pl.col("naive_undercut_sell").sum().alias("uc_naive_ask"),
        (pl.col("new_bid") + pl.col("new_ask")).sum().alias("new_orders"),
        pl.col("msg_total").sum(),
        pl.col("mid_price_sum").sum(),
        pl.col("mid_price_sq_sum").sum(),
        pl.col("mid_obs").sum(),
        pl.col("spread_pts_sum").sum(),
        pl.col("spread_all_obs").sum(),
        # time the book actually spent at each spread width (ms)
        pl.col("spread_ms_1").sum(),
        (pl.col("spread_ms_2") + pl.col("spread_ms_3")
         + pl.col("spread_ms_4p")).sum().alias("spread_ms_wide"),
    ]).sort("window_5m")

    g = g.filter((pl.col("mid_obs") > 0) & (pl.col("spread_all_obs") > 0))
    g = g.with_columns(
        uc_strict=pl.col("uc_strict_bid") + pl.col("uc_strict_ask"),
        avg_midpoint=pl.col("mid_price_sum") / pl.col("mid_obs"),
        avg_spread_pts=pl.col("spread_pts_sum") / pl.col("spread_all_obs"),
        dia=pl.col("window_5m").dt.date(),
    ).with_columns(
        # Var(X) = E[X^2] - E[X]^2 ; clipped at 0 against float noise
        midpoint_volatility=(
            (pl.col("mid_price_sq_sum") / pl.col("mid_obs")
             - (pl.col("mid_price_sum") / pl.col("mid_obs")) ** 2)
            .clip(lower_bound=0.0).sqrt()
        ),
        spread_relativo=pl.col("avg_spread_pts") / (pl.col("mid_price_sum") / pl.col("mid_obs")),
        # share of CLOCK TIME the book was wider than one tick: the regime in
        # which passive quote improvement is geometrically possible at all
        frac_wide=pl.col("spread_ms_wide")
        / (pl.col("spread_ms_1") + pl.col("spread_ms_wide")),
        # Appendix A denominator (all messages) and equation (1) denominator
        qid_msg=pl.col("uc_strict") / pl.col("msg_total"),
        qid_passive=pl.col("uc_strict") / pl.col("new_orders"),
        qid_signed=(pl.col("uc_strict_bid") - pl.col("uc_strict_ask")) / pl.col("new_orders"),
        # --- the other two arms, identical construction ---------------------
        qid_all_msg=pl.col("uc_all") / pl.col("msg_total"),
        qid_all_passive=pl.col("uc_all") / pl.col("new_orders"),
        qid_all_signed=(pl.col("uc_all_bid") - pl.col("uc_all_ask")) / pl.col("new_orders"),
        qid_naive_msg=(pl.col("uc_naive_bid") + pl.col("uc_naive_ask")) / pl.col("msg_total"),
        qid_naive_passive=(pl.col("uc_naive_bid") + pl.col("uc_naive_ask")) / pl.col("new_orders"),
        qid_naive_signed=(pl.col("uc_naive_bid") - pl.col("uc_naive_ask")) / pl.col("new_orders"),
    )
    return g


# ==============================================================================
# 2. LARGE-TICK FILTER + DAILY ORTHOGONALISATION  (thesis eq. 2)
# ==============================================================================
def ortogonalizar(g: pl.DataFrame, coluna: str, filtro: str = "tese",
                  limiar: float = 0.02) -> pl.DataFrame:
    """Apply the large-tick filter, then residualise `coluna` on spread_relativo,
    one OLS per trading day exactly as the thesis Appendix A does.

    filtro="tese"   -- avg_spread_pts > TICK_SIZE, verbatim from Appendix A.
                       NOTE: on this data it excludes nothing (see main()).
    filtro="regime" -- keep windows whose share of clock time with a spread
                       wider than one tick exceeds `limiar`. This is what
                       section 2.2 describes: condition on the regime where
                       passive improvement is geometrically possible.
    """
    if filtro == "regime":
        cond = pl.col("frac_wide") > limiar
    else:
        cond = pl.col("avg_spread_pts") > TICK_SIZE
    f = g.filter(cond & pl.col("spread_relativo").is_not_null()
                 & pl.col(coluna).is_not_null())
    saida = []
    for dia, bloco in f.group_by("dia", maintain_order=True):
        if bloco.height < 10:                     # thesis: skip thin days
            continue
        x = bloco["spread_relativo"].to_numpy()
        y = bloco[coluna].to_numpy()
        beta = np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1)
        alpha = y.mean() - beta * x.mean()
        saida.append(bloco.with_columns(
            pl.Series(f"{coluna}_res", y - (alpha + beta * x))))
    return pl.concat(saida).sort("window_5m") if saida else f.clear()


# ==============================================================================
# 3. IN-SAMPLE (thesis Appendix B)
# ==============================================================================
def na_amostra(d: pl.DataFrame, sinal: str):
    m = d.with_columns(
        ret_5m=((pl.col("avg_midpoint").shift(-1) - pl.col("avg_midpoint"))
                / pl.col("avg_midpoint")) * 10000,
        ret_15m=((pl.col("avg_midpoint").shift(-3) - pl.col("avg_midpoint"))
                 / pl.col("avg_midpoint")) * 10000,
    ).filter(pl.col("ret_5m").is_not_null() & pl.col("ret_15m").is_not_null()
             & pl.col(sinal).is_not_null())

    X = sm.add_constant(m[sinal].to_numpy())
    linhas = []
    for alvo, lags, rotulo in (("ret_5m", 3, "t+1 (5 min)"), ("ret_15m", 6, "t+3 (15 min)")):
        r = sm.OLS(m[alvo].to_numpy(), X).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
        linhas.append((rotulo, r.params[1], r.tvalues[1], r.pvalues[1], r.rsquared, int(r.nobs)))
    return linhas


# ==============================================================================
# 4. OUT-OF-SAMPLE walk-forward + Clark-West (thesis Appendix C)
# ==============================================================================
def fora_da_amostra(d: pl.DataFrame, sinal: str, warmup: int = WARMUP,
                    alvo_abs: bool = False, dummies_hora: bool = False,
                    benchmark_controles: bool = False):
    m = d.with_columns(
        ret_5m=((pl.col("avg_midpoint").shift(-1) - pl.col("avg_midpoint"))
                / pl.col("avg_midpoint")) * 10000
    ).filter(pl.col("ret_5m").is_not_null() & pl.col(sinal).is_not_null())

    y = m["ret_5m"].to_numpy()
    if alvo_abs:
        y = np.abs(y)
    x = m[sinal].to_numpy().reshape(-1, 1)
    if dummies_hora:
        h = m["window_5m"].dt.hour().to_numpy()
        for hh in np.unique(h)[1:]:                 # drop first level
            x = np.hstack([x, (h == hh).astype(float).reshape(-1, 1)])
    X = sm.add_constant(x, has_constant="add")

    if len(y) <= warmup + 10:
        return None
    # restricted model: constant (+ dummies when present) but NO signal
    Xr = np.delete(X, 1, axis=1)
    vt, vb, vm = [], [], []
    for t in range(warmup, len(y)):
        xt, yt = X[:t], y[:t]
        sinal_treino = xt.copy()
        lo, hi = np.percentile(sinal_treino[:, 1], [1, 99])   # winsorise signal
        sinal_treino[:, 1] = np.clip(sinal_treino[:, 1], lo, hi)
        beta, *_ = np.linalg.lstsq(sinal_treino, yt, rcond=None)
        if benchmark_controles and Xr.shape[1] > 1:
            br, *_ = np.linalg.lstsq(Xr[:t], yt, rcond=None)
            pred_b = float(Xr[t] @ br)
        else:
            pred_b = yt.mean()
        vt.append(y[t]); vb.append(pred_b)
        vm.append(float(X[t] @ beta))
    vt, vb, vm = np.array(vt), np.array(vb), np.array(vm)

    mse_b, mse_m = np.mean((vt - vb) ** 2), np.mean((vt - vm) ** 2)
    r2 = 1 - mse_m / mse_b
    adj = (vb - vm) ** 2
    f_t = (vt - vb) ** 2 - ((vt - vm) ** 2 - adj)
    cw = sm.OLS(f_t, np.ones_like(f_t)).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    return {"n_oos": len(vt), "r2_oos": r2, "cw_stat": cw.tvalues[0],
            "cw_p": cw.pvalues[0] / 2, "pred": vm, "real": vt}


def acerto_direcional(res_dir, topo: float):
    """Directional hit rate on the largest `topo` fraction of predicted moves.

    Must be computed from a SIGNED-return model: with an |R| target both the
    prediction and the truth are non-negative and the hit rate is 100% by
    construction, which measures the metric, not the model.
    """
    if res_dir is None:
        return float("nan")
    k = max(1, int(len(res_dir["pred"]) * topo))
    idx = np.argsort(-np.abs(res_dir["pred"]))[:k]
    return float(np.mean(np.sign(res_dir["pred"][idx]) == np.sign(res_dir["real"][idx])))


# ==============================================================================
# 5. MAIN
# ==============================================================================
def main():
    print("Building 5-min panel from the validated 1-min panel...")
    g = construir_painel_5m()
    tot_jan = g.height
    print(f"  {tot_jan:,} five-minute windows in session, "
          f"{g['dia'].n_unique()} trading days")

    # ---- large-tick filter: report what it costs, per thesis section 6.3 ----
    largos = g.filter(pl.col("avg_spread_pts") > TICK_SIZE)
    fora = tot_jan - largos.height
    print(f"  thesis filter (avg_spread_pts > {TICK_SIZE:g}): {largos.height:,} kept, "
          f"{fora:,} dropped ({100*fora/tot_jan:.1f}%)")
    if fora == 0:
        print(f"    !! NON-BINDING: min avg_spread_pts = "
              f"{g['avg_spread_pts'].min():.4f} pts > {TICK_SIZE:g}. A 5-min MEAN")
        print(f"       of a spread that is >= 1 tick always and > 1 tick only "
              f"~{100*g['frac_wide'].mean():.1f}% of the time can never fall to the")
        print(f"       floor, so this filter selects the whole session.")
    print(f"  share of clock time with spread > 1 tick: "
          f"mean {100*g['frac_wide'].mean():.2f}%, "
          f"p5 {100*np.percentile(g['frac_wide'].to_numpy(),5):.2f}%, "
          f"p95 {100*np.percentile(g['frac_wide'].to_numpy(),95):.2f}%")
    reg = g.filter(pl.col("frac_wide") > 0.02)
    print(f"  regime filter (frac_wide > 2%): {reg.height:,} kept, "
          f"{tot_jan - reg.height:,} dropped ({100*(tot_jan-reg.height)/tot_jan:.1f}%)")

    d = ortogonalizar(g, "qid_msg")
    d = d.join(ortogonalizar(g, "qid_passive").select("window_5m", "qid_passive_res"),
               on="window_5m", how="left")
    d = d.join(ortogonalizar(g, "qid_signed").select("window_5m", "qid_signed_res"),
               on="window_5m", how="left")
    d.write_parquet(ARQ_5M)
    print(f"  orthogonalised panel: {d.height:,} windows -> {ARQ_5M.name}\n")

    print("=" * 78)
    print("IN-SAMPLE  (thesis Appendix B / Table 1), HAC Newey-West")
    print("=" * 78)
    for sinal in ("qid_msg_res", "qid_passive_res"):
        print(f"\n  signal = {sinal}")
        print(f"    {'horizon':<14}{'beta':>14}{'z':>9}{'p':>10}{'R2':>9}{'n':>8}")
        for rot, b, z, pv, r2, n in na_amostra(d, sinal):
            print(f"    {rot:<14}{b:>14.4f}{z:>9.3f}{pv:>10.4f}{100*r2:>8.2f}%{n:>8,}")

    print("\n" + "=" * 78)
    print("OUT-OF-SAMPLE  (thesis Appendix C / Table 2), walk-forward + Clark-West")
    print("=" * 78)
    for sinal in ("qid_msg_res", "qid_passive_res"):
        r = fora_da_amostra(d, sinal)
        if r is None:
            print(f"  {sinal}: not enough windows after warm-up ({WARMUP})")
            continue
        print(f"  {sinal:<18} n={r['n_oos']:>5,}  R2_OOS={100*r['r2_oos']:>7.2f}%  "
              f"CW={r['cw_stat']:>6.3f}  p={r['cw_p']:.4f}")

    print("\n" + "=" * 78)
    print("FINAL SPEC  (thesis section 7): signed measure, |R| target, hour dummies")
    print("=" * 78)
    r = fora_da_amostra(d, "qid_signed_res", alvo_abs=True, dummies_hora=True)
    rn = fora_da_amostra(d, "qid_signed_res", alvo_abs=True, dummies_hora=True,
                         benchmark_controles=True)
    if r is None:
        print(f"  not enough windows after warm-up ({WARMUP})")
    else:
        print(f"  vs MEAN benchmark      : n={r['n_oos']:,}  "
              f"R2_OOS={100*r['r2_oos']:>6.2f}%  CW={r['cw_stat']:>6.3f}  p={r['cw_p']:.4f}")
        print(f"  vs DUMMIES benchmark   : n={rn['n_oos']:,}  "
              f"R2_OOS={100*rn['r2_oos']:>6.2f}%  CW={rn['cw_stat']:>6.3f}  p={rn['cw_p']:.4f}")
        print("    ^ the second row is the signal's OWN contribution: the restricted")
        print("      model already contains the hour dummies, so whatever the U-shaped")
        print("      seasonality explains is no longer credited to the signal.")
        # directional accuracy needs a SIGNED-return model
        rd = fora_da_amostra(d, "qid_signed_res", alvo_abs=False, dummies_hora=True)
        for topo in (0.20, 0.10):
            print(f"  directional hit rate (signed-return model), top "
                  f"{int(topo*100)}% events: {100*acerto_direcional(rd, topo):.2f}%")

    print("\n" + "=" * 78)
    print("ABLATION  (thesis Table 3): what each step does to beta")
    print("=" * 78)
    passos = [
        ("1. unsigned, msg denominator", "qid_msg_res", False, False),
        ("2. + signed measure", "qid_signed_res", False, False),
        ("3. + hour dummies", "qid_signed_res", False, True),
        ("4. + |R| target (final)", "qid_signed_res", True, True),
    ]
    for rot, sinal, absoluto, dums in passos:
        m = d.with_columns(
            ret=((pl.col("avg_midpoint").shift(-1) - pl.col("avg_midpoint"))
                 / pl.col("avg_midpoint")) * 10000
        ).filter(pl.col("ret").is_not_null() & pl.col(sinal).is_not_null())
        y = np.abs(m["ret"].to_numpy()) if absoluto else m["ret"].to_numpy()
        x = m[sinal].to_numpy().reshape(-1, 1)
        if dums:
            h = m["window_5m"].dt.hour().to_numpy()
            for hh in np.unique(h)[1:]:
                x = np.hstack([x, (h == hh).astype(float).reshape(-1, 1)])
        fit = sm.OLS(y, sm.add_constant(x, has_constant="add")).fit(
            cov_type="HAC", cov_kwds={"maxlags": 3})
        print(f"  {rot:<32} beta={fit.params[1]:>12.2f}  z={fit.tvalues[1]:>7.3f}  "
              f"R2={100*fit.rsquared:>6.2f}%")
    print("=" * 78)


if __name__ == "__main__":
    main()
