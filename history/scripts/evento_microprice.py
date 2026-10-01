"""
EVENT STUDY — does a strict undercut move the continuous price?

Instead of aggregating undercutting into windows (6,485 observations), this
measures the price response around EVERY strict undercut event (~1.5M), on a
continuous 100 ms price grid, using two depth-weighted prices:

    microprice  = (Qbid*Pask + Qask*Pbid) / (Qbid+Qask)          [L1]
    L3 midprice = same, with Q and P size-weighted over <=3 levels per side

Decomposition, per event:

    mechanical(e)     = P_post - P_pre          the order's OWN effect on the book
    predictive(e, D)  = P(t+D) - P_post         what happens AFTER it, no overlap
    total(e, D)       = P(t+D) - P_pre

`predictive` is the quantity of interest: it starts from the book state the
undercut itself produced, so it cannot contain the mechanical component that
contaminated the window-mean specification (see diagnostico_overlap.py).

Sign convention: responses are signed by the undercut's side (+1 buy, -1 sell),
so a positive mean means "price moves in the direction of the undercutting side".

Inference: event responses overlap heavily and are autocorrelated. Three
standard errors are reported, weakest assumption last:
    (1) naive i.i.d.        — a LOWER BOUND on the SE, never to be quoted alone
    (2) HAC on 1-min block means
    (3) across-day t-test on 12 daily means — most conservative
"""

import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
import undercutting_valida as uv           # noqa: E402

GRID_MS = 100
OUT = Path(__file__).parent / "output_data/evento"
OUT.mkdir(parents=True, exist_ok=True)

# horizons in milliseconds
HORIZONTES_MS = (100, 250, 500, 1_000, 2_000, 5_000, 10_000, 30_000, 60_000)


def processar_dia(caminho: Path) -> Path | None:
    """Replay one day with the continuous grid, save the event arrays."""
    os.environ["POLARS_MAX_THREADS"] = "4"
    data = caminho.stem.split("_")[-1]
    destino = OUT / f"evento_{data}.npz"
    if destino.exists():
        return destino
    r = uv.replay_dia(caminho, bucket="1m", grid_ms=GRID_MS)
    if r is None or "grid" not in r:
        return None
    g = r["grid"]
    np.savez_compressed(
        destino,
        mp=g["mp"].astype(np.float32), l3=g["l3"].astype(np.float32),
        mid=g["mid"].astype(np.float32),
        ev_idx=g["ev_idx"], ev_side=g["ev_side"], ev_size=g["ev_size"],
        ev_tipo=g["ev_tipo"],
        ev_mp_pre=g["ev_mp_pre"].astype(np.float32),
        ev_mp_pos=g["ev_mp_pos"].astype(np.float32),
        ev_l3_pre=g["ev_l3_pre"].astype(np.float32),
        ev_l3_pos=g["ev_l3_pos"].astype(np.float32),
        ev_mid_pre=g["ev_mid_pre"].astype(np.float32),
        ev_mid_pos=g["ev_mid_pos"].astype(np.float32),
        ev_sp_pre=g["ev_sp_pre"], ev_melhora=g["ev_melhora"],
    )
    return destino


def _ffill(a: np.ndarray) -> np.ndarray:
    """Forward-fill NaNs in the price grid (a quiet 100 ms bucket holds the
    previous price, it does not lose it)."""
    idx = np.where(np.isfinite(a), np.arange(a.size), 0)
    np.maximum.accumulate(idx, out=idx)
    return a[idx]


def respostas_dia(npz_path: Path, preco: str = "mp", tipo: int = 1):
    """Signed price responses for one day.  tipo 1 = strict undercut,
    tipo 2 = join-at-best (placebo: adds size at the touch but does NOT
    change the spread)."""
    z = np.load(npz_path)
    grid = _ffill(z[preco].astype(np.float64))
    sel = z["ev_tipo"] == tipo
    idx = z["ev_idx"][sel]
    lado = z["ev_side"][sel]
    sinal = np.where(lado == 0, 1.0, -1.0)          # +1 buy, -1 sell
    pre = z[f"ev_{preco}_pre"].astype(np.float64)[sel]
    pos = z[f"ev_{preco}_pos"].astype(np.float64)[sel]

    ok = np.isfinite(pre) & np.isfinite(pos)
    saida = {"mech": sinal[ok] * (pos[ok] - pre[ok]),
             "idx": idx[ok], "side": lado[ok], "size": z["ev_size"][sel][ok],
             "sp_pre": z["ev_sp_pre"][sel][ok], "melhora": z["ev_melhora"][sel][ok],
             "pos": pos[ok]}
    n = grid.size
    for h in HORIZONTES_MS:
        k = h // GRID_MS
        alvo = np.minimum(idx[ok] + k, n - 1)
        dentro = (idx[ok] + k) < n                   # drop events past session end
        resp = sinal[ok] * (grid[alvo] - pos[ok])
        resp[~dentro] = np.nan
        saida[h] = resp
    return saida


def hac_t(x: np.ndarray, lags: int) -> tuple[float, float]:
    """Newey-West t-stat and SE for the mean of x."""
    import statsmodels.api as sm
    x = x[np.isfinite(x)]
    if x.size < 30:
        return (np.nan, np.nan)
    f = sm.OLS(x, np.ones_like(x)).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return (float(f.tvalues[0]), float(f.bse[0]))


def main():
    arquivos = sorted((uv.OUTPUT_DIR).glob(
        f"undercut_valid_events_{uv.TARGET_SECURITY_ID}_*.parquet"))
    print(f"Building continuous price grids for {len(arquivos)} days "
          f"({GRID_MS} ms resolution)...")
    with ProcessPoolExecutor(max_workers=6) as ex:
        feitos = [p for p in ex.map(processar_dia, arquivos) if p is not None]
    print(f"  {len(feitos)} days ready\n")

    import statsmodels.api as sm
    for preco, rotulo in (("mid", "PLAIN L1 MIDPOINT  [CONTROL: quote-driven only]"),
                          ("mp", "MICROPRICE (L1, size-weighted)"),
                          ("l3", "L3 MIDPRICE (<=3 levels per side)")):
        print("=" * 92)
        print(f"EVENT STUDY — {rotulo}")
        print("=" * 92)

        todos = [respostas_dia(f, preco, tipo=1) for f in feitos]
        placebo = [respostas_dia(f, preco, tipo=2) for f in feitos]

        n_ev = sum(r["mech"].size for r in todos)
        mech = np.concatenate([r["mech"] for r in todos])
        print(f"  strict undercut events: {n_ev:,}   "
              f"(buy {int(sum((r['side']==0).sum() for r in todos)):,} / "
              f"sell {int(sum((r['side']==1).sum() for r in todos)):,})")
        print(f"  MECHANICAL effect of the order itself: "
              f"{mech.mean():+.4f} points (mean, signed)\n")

        print(f"  PREDICTIVE response AFTER the order (excludes the mechanical part)")
        print(f"  {'horizon':>9}{'mean pts':>11}{'t_HAC':>9}{'t_day':>9}"
              f"{'PLACEBO':>11}{'t_day_pl':>10}{'n':>12}")
        print("  " + "-" * 72)
        for h in HORIZONTES_MS:
            x = np.concatenate([r[h] for r in todos])
            x = x[np.isfinite(x)]
            if x.size < 100:
                continue
            m = x.mean()
            se_iid = x.std(ddof=1) / np.sqrt(x.size)
            t_iid = m / se_iid
            # HAC on 1-minute block means
            blocos = []
            for r in todos:
                v = r[h]
                b = r["idx"] // (60_000 // GRID_MS)
                ok = np.isfinite(v)
                if ok.sum() == 0:
                    continue
                bb, vv = b[ok], v[ok]
                ordem = np.argsort(bb, kind="stable")
                bb, vv = bb[ordem], vv[ordem]
                corte = np.flatnonzero(np.diff(bb)) + 1
                blocos.extend([g.mean() for g in np.split(vv, corte)])
            blocos = np.array(blocos)
            lags = max(1, int(np.ceil(h / 60_000)) + 1)
            t_hac, _ = hac_t(blocos, lags)
            # across-day t-test (12 clusters)
            md = np.array([np.nanmean(r[h]) for r in todos])
            md = md[np.isfinite(md)]
            t_day = md.mean() / (md.std(ddof=1) / np.sqrt(md.size)) if md.size > 2 else np.nan
            marca = ""
            if abs(t_day) > 3.106:        # 1% two-sided, df=11
                marca = " **"
            elif abs(t_day) > 2.201:      # 5%
                marca = " *"
            xp = np.concatenate([r[h] for r in placebo]); xp = xp[np.isfinite(xp)]
            mp_ = xp.mean() if xp.size else np.nan
            mdp = np.array([np.nanmean(r[h]) for r in placebo]); mdp = mdp[np.isfinite(mdp)]
            t_dp = mdp.mean()/(mdp.std(ddof=1)/np.sqrt(mdp.size)) if mdp.size > 2 else np.nan
            print(f"  {h/1000:>7.1f}s{m:>11.4f}{t_hac:>9.2f}{t_day:>9.2f}"
                  f"{mp_:>11.4f}{t_dp:>10.2f}{x.size:>12,}{marca}")
        print("  " + "-" * 72)
        print("  PLACEBO = join-at-best orders: add size at the touch, do NOT change the spread")
        print("  t_HAC  = Newey-West on 1-min block means")
        print("  t_day  = across-day t-test, 12 clusters, df=11  (* 5%, ** 1%)")
        print()


if __name__ == "__main__":
    main()
