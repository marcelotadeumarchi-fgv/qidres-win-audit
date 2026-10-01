"""
PROTOCOL ESTIMATION — implements PROTOCOLO_PRE_ESPECIFICACAO.md exactly.

Protocol SHA256: 404c60e902b436a193f3d8c41044fd0785b68a24d90092e1bdf2e4e4ca411ab3
Frozen        : 2026-10-01T02:03:24Z   (before any estimate was produced)

Nothing in this file may deviate from the protocol. Where the protocol fixes a
number (p<0.01, warm-up 500, JANELA_RESIDUO=3, held-out days), it is a module
constant here and is NOT a tunable.

Run order:
    python protocolo_estimar.py train     # primary spec on 09-15..09-26 only
    python protocolo_estimar.py holdout   # reveal 09-29, 09-30 (run ONCE, after train)
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import polars as pl
import statsmodels.api as sm

warnings.filterwarnings("ignore")

# ── fixed by the protocol ────────────────────────────────────────────────────
TARGET_SECURITY_ID = 200001274203
TICK_SIZE = 5.0
SESSAO_INI, SESSAO_FIM = 9.0, 18.0            # §2  BRT, auction excluded
DIAS_HOLDOUT = {"2025-09-29", "2025-09-30"}   # §6  held out at load time
ALPHA_PRIMARIO = 0.01                         # §4  primary decision threshold
WARMUP = 500                                  # §6
WARMUP_GRADE = (300, 500, 700, 900)           # §6  sensitivity
HORIZONTES = (1, 5, 15)                       # §5.1 minutes
SINAL_PRIMARIO_H = 1                          # §4  primary horizon = 1 min
# ─────────────────────────────────────────────────────────────────────────────

BASE = Path(__file__).resolve().parent
PAINEL_1M = BASE / f"output_data/microstructure/undercut_valid_1m_{TARGET_SECURITY_ID}.parquet"
OUT = BASE / "output_data/protocolo"
OUT.mkdir(parents=True, exist_ok=True)

_ESPECIFICACOES_RODADAS = []      # §7.2 — every specification estimated is counted


def carregar(incluir_holdout: bool) -> pl.DataFrame:
    """Load the 1-min panel. Held-out days are dropped HERE (§9 checklist),
    not filtered downstream, so they cannot leak into a fitted object."""
    d = pl.read_parquet(PAINEL_1M)
    d = d.with_columns(brt=pl.col("window_1m") - pl.duration(hours=3))
    d = d.with_columns(
        dia=pl.col("brt").dt.date().cast(pl.String),
        hhmm=pl.col("brt").dt.hour() + pl.col("brt").dt.minute() / 60.0,
    )
    if not incluir_holdout:
        d = d.filter(~pl.col("dia").is_in(list(DIAS_HOLDOUT)))
    # §2 regular session only; auction and after-hours excluded
    d = d.filter((pl.col("hhmm") >= SESSAO_INI) & (pl.col("hhmm") <= SESSAO_FIM))
    return d


def agregar(d: pl.DataFrame, minutos: int) -> pl.DataFrame:
    """Build the protocol's windows (§3). Volume-weighted, signed, microprice."""
    d = d.with_columns(w=pl.col("brt").dt.truncate(f"{minutos}m"))
    g = d.group_by("w").agg([
        # §3.1 numerator, in CONTRACTS, strict
        pl.col("uc_strict_vol_bid").sum(),
        pl.col("uc_strict_vol_ask").sum(),
        # for the §5.2 variants
        pl.col("uc_amb_vol_bid").sum(),
        pl.col("uc_amb_vol_ask").sum(),
        (pl.col("undercut_buy_count") - pl.col("undercut_resid_buy")).sum().alias("uc_strict_n_bid"),
        (pl.col("undercut_sell_count") - pl.col("undercut_resid_sell")).sum().alias("uc_strict_n_ask"),
        # §3.2 denominator, passive order volume
        pl.col("new_vol_bid").sum(),
        pl.col("new_vol_ask").sum(),
        (pl.col("new_bid") + pl.col("new_ask")).sum().alias("new_n"),
        pl.col("msg_total").sum(),
        # §3.4 control: TRUE relative spread
        pl.col("spread_pts_sum").sum(),
        pl.col("spread_all_obs").sum(),
        pl.col("mid_price_sum").sum(),
        pl.col("mid_obs").sum(),
        # §3.5 close-of-window prices
        pl.col("close_microprice").drop_nans().last().alias("mp"),
        pl.col("close_midprice").drop_nans().last().alias("mid"),
        pl.col("dia").first(),
    ]).sort("w")

    g = g.filter((pl.col("mid_obs") > 0) & (pl.col("spread_all_obs") > 0)
                 & pl.col("mp").is_not_null() & pl.col("mp").is_not_nan())
    g = g.with_columns(
        new_vol=pl.col("new_vol_bid") + pl.col("new_vol_ask"),
        avg_spread_pts=pl.col("spread_pts_sum") / pl.col("spread_all_obs"),
        avg_mid=pl.col("mid_price_sum") / pl.col("mid_obs"),
    ).with_columns(
        # §3.3 PRIMARY SIGNAL: signed, strict, volume-weighted
        qid_signed=(pl.col("uc_strict_vol_bid") - pl.col("uc_strict_vol_ask"))
        / pl.col("new_vol"),
        # §3.4 control
        S=pl.col("avg_spread_pts") / pl.col("avg_mid"),
        hora=pl.col("w").dt.hour(),
    )
    return g.filter(pl.col("new_vol") > 0)


def alvo(g: pl.DataFrame, h: int, preco: str = "mp", absoluto: bool = False):
    """§3.5 return: close of window t -> close of window t+h. No overlap with
    the window in which the signal is measured."""
    r = ((pl.col(preco).shift(-h) - pl.col(preco)) / pl.col(preco) * 10000)
    g = g.with_columns(ret=r)
    # a horizon must not span a day boundary
    g = g.with_columns(dia_fim=pl.col("dia").shift(-h))
    g = g.filter((pl.col("dia_fim") == pl.col("dia")) & pl.col("ret").is_not_null()
                 & pl.col("qid_signed").is_not_null() & pl.col("qid_signed").is_finite())
    if absoluto:
        g = g.with_columns(ret=pl.col("ret").abs())
    return g


def desenho(g: pl.DataFrame, sinal: str = "qid_signed", controles: bool = True):
    """Build X exactly as §4: signal + relative spread + hour dummies."""
    x = [g[sinal].to_numpy().reshape(-1, 1)]
    if controles:
        x.append(g["S"].to_numpy().reshape(-1, 1))
        h = g["hora"].to_numpy()
        for hh in np.unique(h)[1:]:
            x.append((h == hh).astype(float).reshape(-1, 1))
    X = np.hstack(x)
    return sm.add_constant(X, has_constant="add")


def estimar(g: pl.DataFrame, rotulo: str, sinal: str = "qid_signed",
            controles: bool = True, maxlags: int = 2):
    X = desenho(g, sinal, controles)
    y = g["ret"].to_numpy()
    fit = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    _ESPECIFICACOES_RODADAS.append(rotulo)
    sd = g[sinal].std()
    return {"rotulo": rotulo, "beta": fit.params[1], "z": fit.tvalues[1],
            "p": fit.pvalues[1], "r2": fit.rsquared, "n": int(fit.nobs),
            # §7.5 standardised effect, in index points, vs the 5-pt crossing cost
            "efeito_1sd_pts": fit.params[1] * sd / 10000 * g["mp"].mean()}


def oos(g: pl.DataFrame, rotulo: str, sinal: str = "qid_signed",
        warmup: int = WARMUP, bench_controles: bool = True, maxlags: int = 3):
    """§6 expanding window. Benchmark of record = mean + controls."""
    X = desenho(g, sinal, controles=True)
    y = g["ret"].to_numpy()
    if len(y) <= warmup + 20:
        return None
    Xr = np.delete(X, 1, axis=1)          # restricted: everything except the signal
    vt, vb, vm = [], [], []
    for t in range(warmup, len(y)):
        xt, yt = X[:t].copy(), y[:t]
        lo, hi = np.percentile(xt[:, 1], [1, 99])      # §4 winsorise on train only
        xt[:, 1] = np.clip(xt[:, 1], lo, hi)
        b, *_ = np.linalg.lstsq(xt, yt, rcond=None)
        if bench_controles and Xr.shape[1] > 1:
            br, *_ = np.linalg.lstsq(Xr[:t], yt, rcond=None)
            pb = float(Xr[t] @ br)
        else:
            pb = yt.mean()
        vt.append(y[t]); vb.append(pb); vm.append(float(X[t] @ b))
    vt, vb, vm = np.array(vt), np.array(vb), np.array(vm)
    r2 = 1 - np.mean((vt - vm) ** 2) / np.mean((vt - vb) ** 2)
    adj = (vb - vm) ** 2
    f = (vt - vb) ** 2 - ((vt - vm) ** 2 - adj)
    cw = sm.OLS(f, np.ones_like(f)).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    _ESPECIFICACOES_RODADAS.append(f"OOS:{rotulo}")
    return {"rotulo": rotulo, "n": len(vt), "r2": r2,
            "cw": cw.tvalues[0], "p": cw.pvalues[0] / 2, "pred": vm, "real": vt}


def acerto(res, topo):
    if res is None:
        return float("nan")
    k = max(1, int(len(res["pred"]) * topo))
    i = np.argsort(-np.abs(res["pred"]))[:k]
    return float(np.mean(np.sign(res["pred"][i]) == np.sign(res["real"][i])))


# ==============================================================================
# REPORTING
# ==============================================================================
def cabecalho(t):
    print("\n" + "=" * 86); print(t); print("=" * 86)


def rodar(incluir_holdout: bool):
    modo = "HOLD-OUT REVEALED (09-29, 09-30)" if incluir_holdout else "TRAINING DAYS ONLY (09-15 .. 09-26)"
    cabecalho(f"PROTOCOL ESTIMATION  —  {modo}")
    print("protocol sha256 404c60e902b436a193f3d8c41044fd0785b68a24d90092e1bdf2e4e4ca411ab3")

    d = carregar(incluir_holdout)
    dias = sorted(set(d["dia"].to_list()))
    print(f"days: {len(dias)}  ({dias[0]} .. {dias[-1]})")

    g1 = agregar(d, 1)
    print(f"1-min windows in session: {g1.height:,}")

    # ---------------- PRIMARY TEST (§4) -------------------------------------
    cabecalho("§4  PRIMARY SPECIFICATION  —  ONE test, decision rule p < 0.01")
    gp = alvo(g1, SINAL_PRIMARIO_H)
    pri = estimar(gp, "primary h=1min")
    print(f"  R(t->t+1min) = a + b*QID_signed(t) + g*S(t) + hour dummies")
    print(f"  signal : strict undercut volume imbalance / passive order volume")
    print(f"  target : microprice return, close(t) -> close(t+1), bp")
    print()
    print(f"    beta            = {pri['beta']:>14.3f}")
    print(f"    z (HAC, lag 2)  = {pri['z']:>14.3f}")
    print(f"    p               = {pri['p']:>14.5f}")
    print(f"    R2              = {100*pri['r2']:>13.3f}%")
    print(f"    n               = {pri['n']:>14,}")
    print(f"    1-sd effect     = {pri['efeito_1sd_pts']:>14.3f} index points "
          f"(vs 5.0 pt crossing cost)")
    print()
    declarado = "beta < 0 (Barardehi mechanism)"
    sinal_ok = pri["beta"] < 0
    passou_p = pri["p"] < ALPHA_PRIMARIO
    print(f"    declared prior  : {declarado}")
    print(f"    p < {ALPHA_PRIMARIO}        : {'YES' if passou_p else 'NO'}")
    print(f"    sign matches    : {'YES' if sinal_ok else 'NO'}")

    # ---------------- §5.1 horizon decay ------------------------------------
    cabecalho("§5.1  HORIZON SET (reported together; no single p interpreted alone)")
    print(f"  {'h':<8}{'beta':>14}{'z':>9}{'p':>10}{'R2':>10}{'n':>9}")
    sinais = []
    for h in HORIZONTES:
        r = estimar(alvo(g1, h), f"h={h}min")
        sinais.append(np.sign(r["beta"]))
        print(f"  {str(h)+'min':<8}{r['beta']:>14.2f}{r['z']:>9.3f}{r['p']:>10.5f}"
              f"{100*r['r2']:>9.3f}%{r['n']:>9,}")
    estavel = len(set(sinais)) == 1
    print(f"\n  sign stable across horizons: {'YES' if estavel else 'NO'}")

    # ---------------- PRIMARY VERDICT ---------------------------------------
    cabecalho("PRIMARY DECISION (§4)")
    suporta = passou_p and estavel
    print(f"  H1 supported only if p < {ALPHA_PRIMARIO} AND sign stable across horizons.")
    print(f"    p < {ALPHA_PRIMARIO}: {passou_p}    sign stable: {estavel}")
    print(f"  >>> H1 {'SUPPORTED' if suporta else 'NOT SUPPORTED'} <<<")
    if suporta and not sinal_ok:
        print("  NOTE: significant with beta > 0 — this CONTRADICTS the declared")
        print("        Barardehi mechanism and is reported as such (§4).")

    # ---------------- §5.2 variants -----------------------------------------
    cabecalho("§5.2  VARIANTS (robustness; isolate one design choice each)")
    g1 = g1.with_columns(
        qid_all=(pl.col("uc_strict_vol_bid") + pl.col("uc_amb_vol_bid")
                 - pl.col("uc_strict_vol_ask") - pl.col("uc_amb_vol_ask")) / pl.col("new_vol"),
        qid_count=(pl.col("uc_strict_n_bid") - pl.col("uc_strict_n_ask")) / pl.col("new_n"),
        qid_msgden=(pl.col("uc_strict_vol_bid") - pl.col("uc_strict_vol_ask")) / pl.col("msg_total"),
    )
    print(f"  {'variant':<34}{'beta':>14}{'z':>9}{'p':>10}{'R2':>10}")
    for rot, sn, pz in (("primary (strict, volume, passive)", "qid_signed", "mp"),
                        ("all verified (strict+ambiguous)", "qid_all", "mp"),
                        ("count-weighted, not volume", "qid_count", "mp"),
                        ("all-message denominator", "qid_msgden", "mp")):
        gg = alvo(g1, SINAL_PRIMARIO_H, preco=pz)
        gg = gg.filter(pl.col(sn).is_finite())
        r = estimar(gg, rot, sinal=sn)
        print(f"  {rot:<34}{r['beta']:>14.2f}{r['z']:>9.3f}{r['p']:>10.5f}{100*r['r2']:>9.3f}%")
    gg = alvo(g1, SINAL_PRIMARIO_H, preco="mid")
    r = estimar(gg, "L1 midpoint target")
    print(f"  {'L1 midpoint target (not micro)':<34}{r['beta']:>14.2f}{r['z']:>9.3f}"
          f"{r['p']:>10.5f}{100*r['r2']:>9.3f}%")

    # ---------------- §6 out-of-sample --------------------------------------
    cabecalho("§6  OUT-OF-SAMPLE — benchmark of record = mean + controls")
    gp = alvo(g1, SINAL_PRIMARIO_H)
    a = oos(gp, "vs mean only", bench_controles=False)
    b = oos(gp, "vs mean + controls (OF RECORD)", bench_controles=True)
    for r in (a, b):
        if r:
            print(f"  {r['rotulo']:<34}n={r['n']:>6,}  R2_OOS={100*r['r2']:>8.3f}%  "
                  f"CW={r['cw']:>6.3f}  p={r['p']:.5f}")
    print(f"\n  warm-up sensitivity (benchmark of record):")
    for w in WARMUP_GRADE:
        r = oos(gp, f"warmup={w}", warmup=w)
        if r:
            print(f"    warm-up {w:<5} n={r['n']:>6,}  R2_OOS={100*r['r2']:>8.3f}%  p={r['p']:.5f}")
    rd = oos(gp, "directional")
    if rd:
        print(f"\n  directional hit rate (signed-return model):")
        for t in (0.20, 0.10):
            print(f"    top {int(t*100):>2}% of predicted moves: {100*acerto(rd, t):.2f}%")

    # ---------------- §5.4 conditional variance ------------------------------
    cabecalho("§5.4  CONDITIONAL VARIANCE (|R|) — dummies benchmark is MANDATORY")
    ga = alvo(g1, SINAL_PRIMARIO_H, absoluto=True)
    m = oos(ga, "|R| vs mean only", bench_controles=False)
    c = oos(ga, "|R| vs mean + controls", bench_controles=True)
    for r in (m, c):
        if r:
            print(f"  {r['rotulo']:<34}R2_OOS={100*r['r2']:>8.3f}%  CW={r['cw']:>6.3f}  p={r['p']:.5f}")
    print("  (only the second line is attributable to the signal — §5.4)")

    # ---------------- §7.2 multiplicity disclosure ---------------------------
    cabecalho("§7.2  SPECIFICATIONS ESTIMATED IN THIS RUN")
    print(f"  total: {len(_ESPECIFICACOES_RODADAS)}")
    for i, e in enumerate(_ESPECIFICACOES_RODADAS, 1):
        print(f"    {i:>2}. {e}")
    print("=" * 86)
    return suporta


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "train"
    if modo not in ("train", "holdout"):
        raise SystemExit("usage: protocolo_estimar.py [train|holdout]")
    rodar(incluir_holdout=(modo == "holdout"))
