"""
ROBUSTNESS FOR THE EVENT STUDY

Two questions the pooled averages cannot answer:

 1. INFERENCE. The headline t-statistics rest on 12 across-day clusters (df=11),
    which is thin. A moving-block bootstrap over 1-minute block means gives a
    confidence interval that does not depend on only 12 observations and that
    tolerates the heavy autocorrelation of overlapping event responses.

 2. CONDITIONAL EFFECTS. A null on the average is consistent with a real effect
    that lives in a subset. Two splits are theoretically motivated:
      * ORDER SIZE — if informed traders express urgency through size, the
        effect should concentrate in large undercuts.
      * SPREAD REGIME — undercutting is only possible when the spread exceeds
        one tick (thesis §2.2). A 2-tick spread allows a 1-tick improvement
        and nothing more; wider spreads allow genuine price discovery.

Everything is measured on the EXCESS over the join-at-best placebo, because the
raw response is dominated by queue mechanics (see evento_microprice.py).
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import evento_microprice as E          # noqa: E402

RNG = np.random.default_rng(20250930)
N_BOOT = 10_000
BLOCO_MIN = 10                          # block length, minutes
MIN_POR_GRID = 60_000 // E.GRID_MS      # grid slots per minute


def blocos_por_minuto(r, h, mascara=None):
    """Mean response per 1-minute block, as (block_id, mean) arrays."""
    v = r[h]
    ok = np.isfinite(v)
    if mascara is not None:
        ok = ok & mascara
    if ok.sum() == 0:
        return np.empty(0, np.int64), np.empty(0)
    b = (r["idx"] // MIN_POR_GRID)[ok]
    vv = v[ok]
    ordem = np.argsort(b, kind="stable")
    b, vv = b[ordem], vv[ordem]
    corte = np.flatnonzero(np.diff(b)) + 1
    ids = b[np.concatenate([[0], corte])] if b.size else np.empty(0, np.int64)
    medias = np.array([g.mean() for g in np.split(vv, corte)]) if b.size else np.empty(0)
    return ids, medias


def serie_excesso(U, P, h, mascara_u=None):
    """Per-day list of (block-aligned) excess series: undercut minus placebo."""
    saida = []
    for ru, rp in zip(U, P):
        mu = None
        if mascara_u is not None:
            mu = mascara_u(ru)
        iu, vu = blocos_por_minuto(ru, h, mu)
        ip, vp = blocos_por_minuto(rp, h)
        if iu.size == 0 or ip.size == 0:
            continue
        comum, a, b = np.intersect1d(iu, ip, return_indices=True)
        if comum.size:
            saida.append(vu[a] - vp[b])
    return saida


def bootstrap_bloco(series_por_dia, n_boot=N_BOOT, bloco=BLOCO_MIN):
    """Moving-block bootstrap on the pooled 1-min excess series.

    Blocks are drawn WITHIN days, so a block never straddles an overnight gap.
    Returns (mean, lo95, hi95, p_two_sided).
    """
    series = [s for s in series_por_dia if s.size >= bloco]
    if not series:
        return (np.nan,) * 4
    media = np.concatenate(series).mean()
    n_total = sum(s.size for s in series)
    n_blocos = max(1, n_total // bloco)

    # pre-build the pool of candidate blocks, one row each
    pool = []
    for s in series:
        for i in range(0, s.size - bloco + 1):
            pool.append(s[i:i + bloco])
    if not pool:
        return (media, np.nan, np.nan, np.nan)
    pool = np.asarray(pool)                      # (n_candidatos, bloco)

    # chunked: the naive (n_boot, n_blocos, bloco) gather would be ~0.5 GB
    pool_media = pool.mean(axis=1)               # block means suffice for a mean
    amostras = np.empty(n_boot)
    passo = 1000
    for a in range(0, n_boot, passo):
        b = min(a + passo, n_boot)
        idx = RNG.integers(0, pool_media.size, size=(b - a, n_blocos))
        amostras[a:b] = pool_media[idx].mean(axis=1)
    lo, hi = np.percentile(amostras, [2.5, 97.5])
    # two-sided p for H0: mean = 0, by the percentile method
    p = 2 * min((amostras <= 0).mean(), (amostras >= 0).mean())
    return (media, lo, hi, p)


def tabela(U, P, rotulo, mascara=None, horizontes=(100, 1_000, 10_000, 60_000)):
    print(f"  {rotulo}")
    print(f"    {'horizon':>8}{'excess':>10}{'boot 95% CI':>22}{'p_boot':>10}{'n_ev':>12}")
    for h in horizontes:
        s = serie_excesso(U, P, h, mascara)
        m, lo, hi, p = bootstrap_bloco(s)
        n = int(sum(np.isfinite(r[h]).sum() if mascara is None
                    else (np.isfinite(r[h]) & mascara(r)).sum() for r in U))
        ci = f"[{lo:+.4f}, {hi:+.4f}]"
        marca = "" if (np.isnan(p) or p > 0.05) else ("**" if p <= 0.01 else "*")
        print(f"    {h/1000:>6.1f}s{m:>10.4f}{ci:>22}{p:>10.4f}{n:>12,}  {marca}")
    print()


def main():
    fs = sorted((Path(__file__).parent / "output_data/evento").glob("evento_*.npz"))
    print(f"{len(fs)} days | {N_BOOT:,} bootstrap replicates | {BLOCO_MIN}-min blocks\n")

    for preco, rot in (("mid", "PLAIN L1 MIDPOINT  [uncontaminated by size weighting]"),
                       ("mp", "MICROPRICE"),
                       ("l3", "L3 MIDPRICE")):
        U = [E.respostas_dia(f, preco, tipo=1) for f in fs]
        P = [E.respostas_dia(f, preco, tipo=2) for f in fs]

        print("=" * 88)
        print(f"{rot}   — EXCESS over join-at-best placebo")
        print("=" * 88)
        tabela(U, P, "ALL strict undercuts")

        # ---- 1. conditional on ORDER SIZE ---------------------------------
        tam = np.concatenate([r["size"] for r in U])
        q80, q95 = np.percentile(tam, [80, 95])
        print(f"  [size split: p80={q80:.0f} lots, p95={q95:.0f} lots, "
              f"max={tam.max():,} lots]")
        tabela(U, P, f"SMALL undercuts (<= {q80:.0f} lots)",
               mascara=lambda r, q=q80: r["size"] <= q)
        tabela(U, P, f"LARGE undercuts (> {q95:.0f} lots, top 5%)",
               mascara=lambda r, q=q95: r["size"] > q)

        # ---- 2. conditional on SPREAD REGIME ------------------------------
        sp = np.concatenate([r["sp_pre"] for r in U])
        d2 = 100 * np.mean(sp == 2)
        print(f"  [spread before the undercut: {d2:.1f}% at exactly 2 ticks, "
              f"{100-d2:.1f}% wider]")
        tabela(U, P, "spread was exactly 2 ticks (1-tick improvement only)",
               mascara=lambda r: r["sp_pre"] == 2)
        tabela(U, P, "spread was 3+ ticks (room for real price discovery)",
               mascara=lambda r: r["sp_pre"] >= 3)
    print("=" * 88)
    print("p_boot: percentile two-sided test of H0 mean excess = 0.  * 5%  ** 1%")
    print("Blocks are drawn within days, so none straddles an overnight gap.")


if __name__ == "__main__":
    main()
