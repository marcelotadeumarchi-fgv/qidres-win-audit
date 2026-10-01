"""
Rodada 2 — Tarefa 3 (EXPLORATÓRIA; numeração E no run_log): decomposição do QID.

Por bloco t (5 min): D = #Deter (todas as causas), T = #DeterExec, I = #Impr,
m_I, m_D = tamanhos médios em ticks, ΔS = variação do spread em ticks.
  θ = T/D     κ = m_D/m_I     borda = ΔS/(D·m_I)
Identidade (assert, para se falhar): I = D·κ − ΔS/m_I.
Blocos com I = 0 ou D = 0: κ/borda indefinidos -> "não identificados" (contados).

Regressões: QID^R e QID^A em três grupos — spline cúbico natural com 4 g.l.
(sem intercepto) em θ, em ln κ e em borda. Nós: fronteira = mín/máx, internos
= quartis 25/50/75 da amostra inteira, fixos no bootstrap. R² decomposto por
Shapley entre os três grupos; IC 95% por bootstrap de pregões (B = 999).
Também: correlação de κ com o comprimento médio das corridas de undercutting
no bloco (corridas da seção 3.1, como no H1: ur_run_len / ur_run_n do painel
de 1 s de spec_fila).
"""
from __future__ import annotations

from itertools import combinations
from math import factorial
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from scipy import stats

from .passo4_diagnosticos import B, SEMENTE

AQ = Path(__file__).parent
SAIDA = AQ / "saida"
QID_DIR = AQ.parent
GRUPOS = ("theta", "ln_kappa", "borda")


def ns_base(x: np.ndarray, nos: np.ndarray) -> np.ndarray:
    """Spline cúbico natural (ESL eq. 5.4–5.5), K nós -> K−1 colunas sem intercepto."""
    K = len(nos)
    def d(k):
        return (np.clip(x - nos[k], 0, None) ** 3 - np.clip(x - nos[K - 1], 0, None) ** 3) / (nos[K - 1] - nos[k])
    cols = [x] + [d(k) - d(K - 2) for k in range(K - 2)]
    return np.column_stack(cols)


def nos_df4(x: np.ndarray) -> np.ndarray:
    return np.array([x.min(), *np.quantile(x, [0.25, 0.5, 0.75]), x.max()])


def r2(y, X):
    X1 = np.column_stack([np.ones(len(y))] + ([X] if X is not None and X.size else []))
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    res = y - X1 @ beta
    sst = ((y - y.mean()) ** 2).sum()
    return 1 - (res @ res) / sst if sst > 0 else np.nan


def shapley(y, bases: dict) -> dict:
    nomes = list(bases)
    n = len(nomes)
    cache = {}
    def R(sub):
        key = tuple(sorted(sub))
        if key not in cache:
            cache[key] = r2(y, np.column_stack([bases[g] for g in key])) if key else 0.0
        return cache[key]
    phi = {}
    for g in nomes:
        outros = [h for h in nomes if h != g]
        v = 0.0
        for s in range(n):
            for S in combinations(outros, s):
                w = factorial(s) * factorial(n - s - 1) / factorial(n)
                v += w * (R(set(S) | {g}) - R(set(S)))
        phi[g] = v
    phi["R2_total"] = R(set(nomes))
    return phi


def preparar(q5: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    d = q5.copy()
    d["I"], d["D"], d["T"] = d.n_impr, d.n_det_all, d.n_det_exec
    ident = (d.I > 0) & (d.D > 0)
    info = {"blocos": len(d), "nao_identificados_I0_ou_D0": int((~ident).sum())}
    d = d[ident].copy()
    d["theta"] = d["T"] / d["D"]
    d["kappa"] = d.m_D / d.m_I
    d["borda"] = d.delta_spread_ticks / (d.D * d.m_I)
    d["ln_kappa"] = np.log(d.kappa)
    lado_dir = d.D * d.kappa - d.delta_spread_ticks / d.m_I
    erro = (d.I - lado_dir).abs()
    info["identidade_erro_max"] = float(erro.max())
    assert (erro < 1e-9 * np.maximum(1, d.I)).all(), "identidade I = D·κ − ΔS/m_I falhou: PARAR"
    return d, info


def corridas_5min() -> pd.DataFrame:
    p = pl.read_parquet(QID_DIR / "output_data/spec_fila/painel_200001274203.parquet")
    p = p.with_columns(brt=pl.col("window_1m") - pl.duration(hours=3)).with_columns(w=pl.col("brt").dt.truncate("5m"))
    g = p.group_by("w").agg((pl.col("ur_run_len_bid") + pl.col("ur_run_len_ask")).sum().alias("len"),
                            (pl.col("ur_run_n_bid") + pl.col("ur_run_n_ask")).sum().alias("n"))
    g = g.with_columns(comp_medio=pl.when(pl.col("n") > 0).then(pl.col("len") / pl.col("n")),
                       dia=pl.col("w").dt.date().cast(pl.String),
                       bloco_ini_ms=pl.col("w").dt.hour() * 3_600_000 + pl.col("w").dt.minute() * 60_000)
    return g.select("dia", "bloco_ini_ms", "comp_medio", pl.col("n").alias("n_corridas")).to_pandas()


def main():
    rng = np.random.default_rng(SEMENTE)
    q5 = pd.read_parquet(SAIDA / "qid_5min.parquet")
    d, info = preparar(q5)
    nos = {g: nos_df4(d[g].to_numpy()) for g in GRUPOS}
    base = lambda df: {g: ns_base(df[g].to_numpy(), nos[g]) for g in GRUPOS}
    linhas = []
    for y in ("qid_R", "qid_A"):
        dd = d.dropna(subset=[y])
        pt = shapley(dd[y].to_numpy(), base(dd))
        dias = dd.dia.unique()
        grp = {k: v for k, v in dd.groupby("dia")}
        bs = []
        for _ in range(B):
            a = pd.concat([grp[k] for k in rng.choice(dias, len(dias), replace=True)])
            bs.append(shapley(a[y].to_numpy(), base(a)))
        for k in list(GRUPOS) + ["R2_total"]:
            v = np.array([b[k] for b in bs])
            linhas.append({"y": y, "componente": k, "shapley_R2": pt[k],
                           "fracao_do_R2": pt[k] / pt["R2_total"] if k != "R2_total" else 1.0,
                           "ic95_inf": np.nanpercentile(v, 2.5), "ic95_sup": np.nanpercentile(v, 97.5),
                           "N": len(dd), "n_dias": len(dias)})
    out = pd.DataFrame(linhas)
    # κ × comprimento médio das corridas
    c = d.merge(corridas_5min(), on=["dia", "bloco_ini_ms"], how="left")
    cc = c.dropna(subset=["comp_medio", "kappa"])
    rp = float(np.corrcoef(cc.kappa, cc.comp_medio)[0, 1]) if len(cc) > 2 else np.nan
    rs = float(stats.spearmanr(cc.kappa, cc.comp_medio).statistic) if len(cc) > 2 else np.nan
    dias = cc.dia.unique()
    grp = {k: v for k, v in cc.groupby("dia")}
    bs = [np.corrcoef(*[pd.concat([grp[k] for k in rng.choice(dias, len(dias), replace=True)])[x]
                        for x in ("kappa", "comp_medio")])[0, 1] for _ in range(B)]
    corr = {"y": "kappa × comprimento médio das corridas", "componente": "pearson", "shapley_R2": rp,
            "fracao_do_R2": np.nan, "ic95_inf": np.nanpercentile(bs, 2.5), "ic95_sup": np.nanpercentile(bs, 97.5),
            "N": len(cc), "n_dias": len(dias), "spearman": rs,
            "blocos_sem_corrida": int(c.comp_medio.isna().sum())}
    out = pd.concat([out, pd.DataFrame([corr])], ignore_index=True)
    out.to_csv(SAIDA / "decomposicao_qid_shapley.csv", index=False)
    d[["dia", "bloco_ini", "I", "D", "T", "m_I", "m_D", "theta", "kappa", "borda", "qid_R", "qid_A"]] \
        .to_parquet(SAIDA / "decomposicao_qid_blocos.parquet", index=False)
    return out, info


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    o, i = main()
    print(i); print(o.to_string(index=False))
