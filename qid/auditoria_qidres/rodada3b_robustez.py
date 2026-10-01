"""
Rodada 3b — robustez exploratória (nada confirmatório; pré-registro inalterado).

E2 — robustez do E1 (confiabilidade da seleção adversa sem sobreposição):
  (a) bloco de 5 min, intervalo entre metades g ∈ {0, 30, 60, 120} s;
  (b) blocos de 5, 10 e 15 min, metades proporcionais ((L − g)/2 cada), g = 60 s;
  (c) estimador alternativo: correlação de AS entre o bloco t e o bloco t+2
      (mesmo pregão, blocos consecutivos), para L = 5, 10, 15 min.
  Tudo o mais igual ao E1: amostra principal (sinal exato pelo book), AS
  ponderado pela quantidade, versões bruta e sem média por hora, IC por wild
  cluster bootstrap por sessão (Webb, B = 9.999), Spearman-Brown nas metades.
E3 — θ e κ:
  correlação θ_t × κ_t (por L); equação do Passo 2 com θ_t e κ_t juntos,
  h = 30 e 60 s, blocos de 5, 10 e 15 min; β, IC (Webb e HAC), partial R² de cada.
Grade de blocos de L min: alinhada ao relógio, só blocos inteiros dentro da
janela válida, descartando os que cruzam exclusões (como I8).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .agregacao import agregar_periodo
from .pipeline import janela_valida
from .rodada3_viabilidade import (B_WILD, HS, PILOTO, SAIDA, WEBB, _z, negocios_com_pi, spearman_brown,
                                  wild_beta)

SEMENTE = 20261005
MIN = 60_000
TAMANHOS = (5, 10, 15)


def blocos_L(dia: str, L: int):
    ini, fim, exc = janela_valida(dia)
    Lm = L * MIN
    a = -(-ini // Lm) * Lm
    out = []
    while a + Lm <= fim:
        b = a + Lm
        if not any(x < b and a < y for x, y in exc):
            out.append((a, b))
        a = b
    return out


def atribuir(ng: pd.DataFrame, L: int) -> pd.DataFrame:
    partes = []
    for dia, g in ng.groupby("dia"):
        bl = blocos_L(dia, L)
        ini_b = np.array([x for x, _ in bl])
        t = g.t_ms.to_numpy()
        k = np.searchsorted(ini_b, t, "right") - 1
        ok = (k >= 0) & (t < ini_b[np.clip(k, 0, None)] + L * MIN)
        g = g[ok].copy()
        g["bloco"] = ini_b[k[ok]]
        partes.append(g)
    return pd.concat(partes, ignore_index=True)


def _wavg(g, col):
    return np.average(g[col], weights=g.vol)


def wild_r(x, y, cl, rng, B=B_WILD):
    X = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    yh, u = X @ beta, y - X @ beta
    grupos, gi = np.unique(cl, return_inverse=True)
    W = rng.choice(WEBB, size=(B, len(grupos)))
    Y = yh[None, :] + W[:, gi] * u[None, :]
    xc = x - x.mean()
    Yc = Y - Y.mean(1, keepdims=True)
    r = (Yc @ xc) / (np.sqrt((Yc ** 2).sum(1)) * np.sqrt((xc ** 2).sum()))
    return np.percentile(r, [2.5, 97.5])


def _versoes(df, ca, cb):
    x, y = df[ca].to_numpy(), df[cb].to_numpy()
    yield "bruto", x, y
    h = df.bloco // 3_600_000
    yield "sem_media_por_hora", x - df.groupby(h)[ca].transform("mean").to_numpy(), \
        y - df.groupby(h)[cb].transform("mean").to_numpy()


def metades(ngL: pd.DataFrame, L: int, gap_s: int, h: int, rng) -> list[dict]:
    meia = (L * MIN - gap_s * 1000) / 2
    v = ngL.dropna(subset=[f"PI_{h}"]).copy()
    v["pos"] = v.t_ms - v.bloco
    A = v[v.pos < meia]
    Bm = v[v.pos >= L * MIN - meia]
    ga = A.groupby(["dia", "bloco"]).apply(lambda g: pd.Series({"A": _wavg(g, f"PI_{h}"), "nA": len(g)}))
    gb = Bm.groupby(["dia", "bloco"]).apply(lambda g: pd.Series({"B": _wavg(g, f"PI_{h}"), "nB": len(g)}))
    m = pd.concat([ga, gb], axis=1).dropna().reset_index()
    out = []
    for versao, x, y in _versoes(m, "A", "B"):
        r = float(np.corrcoef(x, y)[0, 1])
        ic = wild_r(x, y, m.dia.to_numpy(), rng)
        out.append({"estimador": "metades sem sobreposição (Spearman-Brown)", "bloco_min": L, "intervalo_s": gap_s,
                    "h_s": h, "versao": versao, "valor": spearman_brown(r), "r_bruto": r,
                    "ic95_inf": spearman_brown(ic[0]), "ic95_sup": spearman_brown(ic[1]),
                    "N_blocos": len(m), "n_sessoes": m.dia.nunique(),
                    "negocios_medios_por_metade": float((m.nA.mean() + m.nB.mean()) / 2)})
    return out


def t_mais_2(ngL: pd.DataFrame, L: int, h: int, rng) -> list[dict]:
    v = ngL.dropna(subset=[f"PI_{h}"])
    s = v.groupby(["dia", "bloco"]).apply(lambda g: pd.Series({"AS": _wavg(g, f"PI_{h}"), "n": len(g)})).reset_index()
    s = s.sort_values(["dia", "bloco"])
    sh = s.groupby("dia")[["AS", "bloco", "n"]].shift(-2)
    ok = sh.bloco == s.bloco + 2 * L * MIN
    m = pd.DataFrame({"dia": s.dia[ok], "bloco": s.bloco[ok], "A": s.AS[ok], "B": sh.AS[ok],
                      "nA": s.n[ok], "nB": sh.n[ok]})
    out = []
    for versao, x, y in _versoes(m, "A", "B"):
        r = float(np.corrcoef(x, y)[0, 1])
        ic = wild_r(x, y, m.dia.to_numpy(), rng)
        out.append({"estimador": "correlação AS_t × AS_{t+2}", "bloco_min": L, "intervalo_s": np.nan, "h_s": h,
                    "versao": versao, "valor": r, "r_bruto": r, "ic95_inf": ic[0], "ic95_sup": ic[1],
                    "N_blocos": len(m), "n_sessoes": m.dia.nunique(),
                    "negocios_medios_por_metade": float((m.nA.mean() + m.nB.mean()) / 2)})
    return out


# ------------------------------------------------------------------ E3
def painel_L(dia: str, L: int, ngL: pd.DataFrame) -> pd.DataFrame:
    dd = dia.replace("-", "")
    ev = pd.read_parquet(PILOTO / "por_dia" / f"events_classified_{dd}.parquet")
    es = pd.read_parquet(PILOTO / "por_dia" / f"estados_{dd}.parquet")
    ng = pd.read_parquet(PILOTO / "por_dia" / f"negocios_{dd}.parquet")
    base = float(ev.b_pts.iloc[0] - ev.b.iloc[0] * 5.0)
    _, _, exc = janela_valida(dia)
    t, mid = es.t_ms.to_numpy(), (es.a.to_numpy() + es.b.to_numpy()) / 2.0
    linhas = []
    for a, b in blocos_L(dia, L):
        lin = agregar_periodo(ev, es, ng, a, b, {"dia": dia, "bloco": a}, exc, base)
        i0, i1 = np.searchsorted(t, a, "left"), np.searchsorted(t, b, "left")
        lin["r"] = mid[i1 - 1] - mid[i0 - 1] if i0 > 0 and i1 > 0 else np.nan
        linhas.append(lin)
    p = pd.DataFrame(linhas)
    p["theta"] = p.n_det_exec / p.n_det_all.replace(0, np.nan)
    p["kappa"] = p.m_D / p.m_I
    p["abs_r"] = p.r.abs()
    p["hora"] = p.bloco // 3_600_000
    g = ngL[ngL.dia == dia]
    for h in (30, 60):
        v = g.dropna(subset=[f"PI_{h}"])
        p = p.merge(v.groupby("bloco").apply(lambda x: _wavg(x, f"PI_{h}")).rename(f"AS_{h}").reset_index(),
                    on="bloco", how="left")
    return p[["dia", "bloco", "hora", "theta", "kappa", "volume", "qvol", "abs_r", "frac_1tick", "AS_30", "AS_60"]]


def conjunta(p: pd.DataFrame, L: int, h: int, rng) -> list[dict]:
    p = p.sort_values(["dia", "bloco"]).copy()
    sh = p.groupby("dia")[[f"AS_{h}", "bloco"]].shift(-1)
    p["y"] = np.where(sh.bloco == p.bloco + L * MIN, sh[f"AS_{h}"], np.nan)
    ctrl = [f"AS_{h}", "volume", "qvol", "abs_r", "frac_1tick"]
    d = p.dropna(subset=["y", "theta", "kappa"] + ctrl).copy()
    for c in ["y", "theta", "kappa"] + ctrl:
        d[c] = _z(d[c])
    fe = pd.get_dummies(d.hora.astype(str), drop_first=True).to_numpy(float)
    regs = ["theta", "kappa"]
    X = np.column_stack([np.ones(len(d)), d[regs].to_numpy(), d[ctrl].to_numpy(float), fe])
    y = d.y.to_numpy()
    hac = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
    ssr = lambda A: float(((y - A @ np.linalg.lstsq(A, y, rcond=None)[0]) ** 2).sum())
    out = []
    for j, nome in enumerate(regs, start=1):
        b, ic, ep = wild_beta(y, X, d.dia.to_numpy(), j, rng)
        Xr = np.delete(X, j, axis=1)
        out.append({"bloco_min": L, "h_s": h, "regressor": "θ_t" if nome == "theta" else "κ_t", "beta": b,
                    "ic95_webb_inf": ic[0], "ic95_webb_sup": ic[1],
                    "ic95_hac_inf": hac.conf_int()[j][0], "ic95_hac_sup": hac.conf_int()[j][1],
                    "partial_R2": (ssr(Xr) - ssr(X)) / ssr(Xr), "N_blocos": len(d), "n_sessoes": d.dia.nunique()})
    return out


def main():
    rng = np.random.default_rng(SEMENTE)
    dias = pd.read_csv(PILOTO / "passo3_info_por_dia.csv").dia.tolist()
    ng = pd.concat([negocios_com_pi(d, "principal") for d in dias], ignore_index=True)
    E2 = []
    porL = {L: atribuir(ng, L) for L in TAMANHOS}
    for h in HS:
        for g in (0, 30, 60, 120):                       # (a)
            E2 += metades(porL[5], 5, g, h, rng)
        for L in (10, 15):                               # (b) — L=5, g=60 já está em (a)
            E2 += metades(porL[L], L, 60, h, rng)
        for L in TAMANHOS:                               # (c)
            E2 += t_mais_2(porL[L], L, h, rng)
    e2 = pd.DataFrame(E2)
    e2.insert(0, "id", "E2")
    e2.to_csv(SAIDA / "robustez_E2_confiabilidade.csv", index=False)

    E3, cor = [], []
    for L in TAMANHOS:
        p = pd.concat([painel_L(d, L, porL[L]) for d in dias], ignore_index=True)
        p.to_parquet(SAIDA / f"rodada3/painel_theta_kappa_{L}min.parquet", index=False)
        cc = p.dropna(subset=["theta", "kappa"])
        r = float(np.corrcoef(cc.theta, cc.kappa)[0, 1])
        ic = wild_r(cc.theta.to_numpy(), cc.kappa.to_numpy(), cc.dia.to_numpy(), rng)
        cor.append({"bloco_min": L, "h_s": np.nan, "regressor": "corr(θ_t, κ_t)", "beta": r,
                    "ic95_webb_inf": ic[0], "ic95_webb_sup": ic[1], "N_blocos": len(cc), "n_sessoes": cc.dia.nunique()})
        for h in (30, 60):
            E3 += conjunta(p, L, h, rng)
    e3 = pd.concat([pd.DataFrame(cor), pd.DataFrame(E3)], ignore_index=True)
    e3.insert(0, "id", "E3")
    e3.to_csv(SAIDA / "robustez_E3_theta_kappa.csv", index=False)
    return e2, e3


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    a, b = main()
    print(a.round(3).to_string(index=False)); print(b.round(4).to_string(index=False))
