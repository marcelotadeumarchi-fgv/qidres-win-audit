"""
Rodada 2 — Tarefa 2 (confirmatória): causalidade reversa nas QIDs, 5 min.

Para cada medida M (bloco t):
  Para frente : r_{t+1} = α + β·M_t + Σ_{k=1..5} γ_k r_{t−k} + Σ_{k=1..5} δ_k OFI_{t−k} + ε
  Reação      : M_t     = α + λ0·r_t + λ1·r_{t−1} + Σ_{k=1..5} φ_k M_{t−k} + u
(controles exatamente como na instrução: na equação para frente, r_{t−1..t−5};
r_t NÃO entra.)

r_t   : variação do midpoint dentro do bloco, em ticks (estado vigente no início
        do bloco → último estado do bloco).
OFI_t : Cont, Kukanov e Stoikov (2014), somado sobre os eventos de casamento do
        bloco: e_n = 1{b_n ≥ b_{n−1}} q^b_n − 1{b_n ≤ b_{n−1}} q^b_{n−1}
                     − 1{a_n ≤ a_{n−1}} q^a_n + 1{a_n ≥ a_{n−1}} q^a_{n−1}
        (pares que cruzam um estado de referência pós-leilão são descartados).
Blocos não cruzam pregões nem leilões: defasagens/avanços exigem blocos
consecutivos (Δ = 5 min) do mesmo pregão.

Medidas (decisão do usuário):
  principal  : QIDres^R, QIDres^A, BQIDres, AQIDres — versão intra_oos.
  robustez   : QIDres^R, QIDres^A (intra_oos) + BQID e AQID brutos.
Partial R² = (SSR_restrito − SSR_completo)/SSR_restrito. IC 95% por bootstrap
reamostrando pregões inteiros (B = 999), inclusive da diferença.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .passo2_um_pregao import BLOCO_MS, blocos_5min
from .passo4_diagnosticos import B, SEMENTE
from .pipeline import janela_valida

SAIDA = Path(__file__).parent / "saida"
DIAS_DIR = SAIDA / "por_dia"
L = 5


def ofi_eventos(es: pd.DataFrame) -> np.ndarray:
    b, a = es.b.to_numpy(), es.a.to_numpy()
    qb, qa = es.qb.to_numpy().astype(float), es.qa.to_numpy().astype(float)
    e = np.zeros(len(es))
    e[1:] = ((b[1:] >= b[:-1]) * qb[1:] - (b[1:] <= b[:-1]) * qb[:-1]
             - (a[1:] <= a[:-1]) * qa[1:] + (a[1:] >= a[:-1]) * qa[:-1])
    e[es.referencia.to_numpy()] = 0.0       # par que cruza leilão/exclusão
    return e


def blocos_r_ofi(dia: str, es: pd.DataFrame | None = None) -> pd.DataFrame:
    if es is None:
        es = pd.read_parquet(DIAS_DIR / f"estados_{dia.replace('-', '')}.parquet")
    ini, fim, exc = janela_valida(dia)
    t = es.t_ms.to_numpy()
    mid = (es.a.to_numpy() + es.b.to_numpy()) / 2.0
    e = ofi_eventos(es)
    cum = np.concatenate([[0.0], np.cumsum(e)])
    out = []
    for x, y in blocos_5min(ini, fim, exc):
        i0 = np.searchsorted(t, x, "left")
        i1 = np.searchsorted(t, y, "left")
        if i0 == 0 or i1 == 0:
            continue
        out.append({"dia": dia, "ms": x, "r": mid[i1 - 1] - mid[i0 - 1], "ofi": cum[i1] - cum[i0]})
    return pd.DataFrame(out)


def painel_defasado(df: pd.DataFrame, col: str, ks) -> pd.DataFrame:
    """Defasagens/avanços só entre blocos consecutivos do mesmo pregão."""
    df = df.sort_values(["dia", "ms"]).copy()
    for k in ks:
        sh = df.groupby("dia")[[col, "ms"]].shift(k)
        nome = f"{col}_L{k}" if k > 0 else f"{col}_F{-k}"
        df[nome] = np.where(sh["ms"] == df["ms"] - k * BLOCO_MS, sh[col], np.nan)
    return df


def _ssr(y, X):
    X = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ beta
    return float(r @ r), beta


def partial_r2(y, X_completo, X_restrito):
    s_c, beta = _ssr(y, X_completo)
    s_r, _ = _ssr(y, X_restrito)
    return (s_r - s_c) / s_r if s_r > 0 else np.nan, beta


def estimar(d: pd.DataFrame) -> dict:
    fw = ["M"] + [f"r_L{k}" for k in range(1, L + 1)] + [f"ofi_L{k}" for k in range(1, L + 1)]
    f = d.dropna(subset=["r_F1"] + fw)
    rv = ["r", "r_L1"] + [f"M_L{k}" for k in range(1, L + 1)]
    g = d.dropna(subset=["M"] + rv)
    pr_f, _ = partial_r2(f.r_F1.to_numpy(), f[fw].to_numpy(), f[fw[1:]].to_numpy())
    pr_r, beta = partial_r2(g.M.to_numpy(), g[rv].to_numpy(), g[rv[2:]].to_numpy())
    return {"pr2_frente": pr_f, "pr2_reacao": pr_r, "dif_reacao_menos_frente": pr_r - pr_f,
            "lambda0": float(beta[1]), "lambda1": float(beta[2]), "N_frente": len(f), "N_reacao": len(g),
            "n_dias": d.dia.nunique()}


def montar(medida: pd.DataFrame, rof: pd.DataFrame) -> pd.DataFrame:
    d = rof.merge(medida, on=["dia", "ms"], how="left")
    d = painel_defasado(d, "r", [-1] + list(range(1, L + 1)))
    d = painel_defasado(d, "ofi", range(1, L + 1))
    d = painel_defasado(d, "M", range(1, L + 1))
    return d


def bootstrap(d: pd.DataFrame, rng) -> dict:
    dias = d.dia.unique()
    grp = {k: v for k, v in d.groupby("dia")}
    vals = {k: [] for k in ("pr2_frente", "pr2_reacao", "dif_reacao_menos_frente", "lambda0")}
    for _ in range(B):
        pedacos = []
        for j, k in enumerate(rng.choice(dias, len(dias), replace=True)):
            p = grp[k].copy()
            p["dia"] = f"{k}#{j}"          # cópias do mesmo pregão contam como pregões distintos
            pedacos.append(p)
        r = estimar(pd.concat(pedacos))
        for k in vals:
            vals[k].append(r[k])
    return {f"{k}_ic95": (float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))) for k, v in vals.items()}


def main():
    rng = np.random.default_rng(SEMENTE)
    q5 = pd.read_parquet(SAIDA / "qid_5min.parquet")
    dias = sorted(q5.dia.unique())
    rof = pd.concat([blocos_r_ofi(d) for d in dias], ignore_index=True)
    rof.to_parquet(SAIDA / "retorno_ofi_5min.parquet", index=False)
    intra = pd.read_parquet(SAIDA / "qidres_5min_intradiaria.parquet")
    oos = intra[intra.modo == "intra_oos"].rename(columns={"bloco_ini_ms": "ms"})
    q5m = q5.rename(columns={"bloco_ini_ms": "ms"})
    especs = []
    for v, rot in (("qid_R", "QIDres^R"), ("qid_A", "QIDres^A"), ("bqid_R", "BQIDres"), ("aqid_R", "AQIDres")):
        especs.append(("principal", rot, oos[oos.versao == v][["dia", "ms", "qidres"]].rename(columns={"qidres": "M"})))
    for v, rot in (("qid_R", "QIDres^R"), ("qid_A", "QIDres^A")):
        especs.append(("robustez", rot, oos[oos.versao == v][["dia", "ms", "qidres"]].rename(columns={"qidres": "M"})))
    for v, rot in (("bqid_R", "BQID bruto"), ("aqid_R", "AQID bruto")):
        especs.append(("robustez", rot, q5m[["dia", "ms", v]].rename(columns={v: "M"})))
    linhas = []
    for grupo, rot, med in especs:
        d = montar(med, rof[rof.dia.isin(med.dia.unique())])
        r = estimar(d)
        r.update(bootstrap(d, rng))
        r.update({"analise": grupo, "medida": rot})
        linhas.append(r)
    out = pd.DataFrame(linhas)
    for k in ("pr2_frente", "pr2_reacao", "dif_reacao_menos_frente", "lambda0"):
        out[f"{k}_ic95_inf"] = out[f"{k}_ic95"].map(lambda x: x[0])
        out[f"{k}_ic95_sup"] = out[f"{k}_ic95"].map(lambda x: x[1])
        out = out.drop(columns=f"{k}_ic95")
    out["sinal_lambda0"] = np.sign(out.lambda0).map({1.0: "+", -1.0: "−", 0.0: "0"})
    cols = ["analise", "medida", "N_frente", "N_reacao", "n_dias",
            "pr2_frente", "pr2_frente_ic95_inf", "pr2_frente_ic95_sup",
            "pr2_reacao", "pr2_reacao_ic95_inf", "pr2_reacao_ic95_sup",
            "dif_reacao_menos_frente", "dif_reacao_menos_frente_ic95_inf", "dif_reacao_menos_frente_ic95_sup",
            "lambda0", "lambda0_ic95_inf", "lambda0_ic95_sup", "sinal_lambda0", "lambda1"]
    out[cols].to_csv(SAIDA / "causalidade_reversa_qid.csv", index=False)
    return out[cols]


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    print(main().to_string(index=False))
