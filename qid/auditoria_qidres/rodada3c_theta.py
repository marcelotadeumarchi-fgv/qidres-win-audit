"""
E4 (exploratório) — causalidade reversa do θ. Último teste nos 12 pregões.

Blocos de 5, 10, 15 min; h = 30 e 60 s; variáveis padronizadas (DP = 1) em
cada amostra de regressão; defasagens só entre blocos consecutivos do mesmo
pregão (mesmas exclusões de sessão/leilão).

Para frente: AS_{t+1,h} = α + β·θ_t + γ0·AS_{t,h} + γ1·AS_{t−1,h}
             + volume_t + qvol_t + qvol_{t−1} + |r_t| + |r_{t−1}| + frac_1tick_t + FE hora + ε
Reação:      θ_t = α + λ0·|r_t| + λ1·|r_{t−1}| + λ2·AS_{t−1,h} + λ3·qvol_{t−1}
             + θ_{t−1..t−3} + volume_t + frac_1tick_t + FE hora + u
Secundário:  mesma reação para θ^B e θ^A (um lado só), com r_t e r_{t−1} com sinal.

β e λ: IC 95% wild cluster bootstrap por sessão (Webb, B = 9.999) e HAC
(Newey-West, 5). Partial R² de θ (frente) e conjunto de (|r_t|, |r_{t−1}|,
AS_{t−1}, qvol_{t−1}) (reação). Diferença (reação − frente): IC 95% por
bootstrap de pares por sessão (reamostra pregões inteiros, B = 9.999).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .agregacao import agregar_periodo
from .pipeline import janela_valida
from .rodada3_viabilidade import PILOTO, SAIDA, _z, negocios_com_pi, wild_beta
from .rodada3b_robustez import MIN, TAMANHOS, _wavg, atribuir, blocos_L

SEMENTE = 20261006
B = 9999
HS3 = (30, 60)


def painel(dia: str, L: int, ngL: pd.DataFrame) -> pd.DataFrame:
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
    nz = lambda s: s.replace(0, np.nan)
    p["theta"] = p.n_det_exec / nz(p.n_det_all)
    p["theta_B"] = p.bid_n_det_exec / nz(p.bid_n_det_all)
    p["theta_A"] = p.ask_n_det_exec / nz(p.ask_n_det_all)
    p["abs_r"] = p.r.abs()
    p["hora"] = p.bloco // 3_600_000
    g = ngL[ngL.dia == dia]
    for h in HS3:
        v = g.dropna(subset=[f"PI_{h}"])
        p = p.merge(v.groupby("bloco").apply(lambda x: _wavg(x, f"PI_{h}")).rename(f"AS_{h}").reset_index(),
                    on="bloco", how="left")
    return p[["dia", "bloco", "hora", "theta", "theta_B", "theta_A", "volume", "qvol", "r", "abs_r",
              "frac_1tick", "AS_30", "AS_60"]]


def defasar(p: pd.DataFrame, L: int, col: str, k: int) -> np.ndarray:
    sh = p.groupby("dia")[[col, "bloco"]].shift(k)
    return np.where(sh.bloco == p.bloco - k * L * MIN, sh[col], np.nan)


def _fit(d, y, regs, ctrl):
    fe = pd.get_dummies(d.hora.astype(str), drop_first=True).to_numpy(float)
    X = np.column_stack([np.ones(len(d)), d[regs].to_numpy(float), d[ctrl].to_numpy(float), fe])
    Xr = np.column_stack([np.ones(len(d)), d[ctrl].to_numpy(float), fe])
    yv = d[y].to_numpy(float)
    ssr = lambda A, yy: float(((yy - A @ np.linalg.lstsq(A, yy, rcond=None)[0]) ** 2).sum())
    return yv, X, Xr, ssr


def preparar(p: pd.DataFrame, L: int, h: int, theta_col: str, sinal: bool) -> pd.DataFrame:
    p = p.sort_values(["dia", "bloco"]).copy()
    p["y_lead"] = defasar(p, L, f"AS_{h}", -1)
    p["AS_L1"] = defasar(p, L, f"AS_{h}", 1)
    p["qvol_L1"] = defasar(p, L, "qvol", 1)
    p["abs_r_L1"] = defasar(p, L, "abs_r", 1)
    p["r_L1"] = defasar(p, L, "r", 1)
    for k in (1, 2, 3):
        p[f"th_L{k}"] = defasar(p, L, theta_col, k)
    p["th"] = p[theta_col]
    p["AS0"] = p[f"AS_{h}"]
    return p


FR_REGS, FR_CTRL = ["th"], ["AS0", "AS_L1", "volume", "qvol", "qvol_L1", "abs_r", "abs_r_L1", "frac_1tick"]


def re_spec(sinal: bool):
    rr = ["r", "r_L1"] if sinal else ["abs_r", "abs_r_L1"]
    return rr + ["AS_L1", "qvol_L1"], ["th_L1", "th_L2", "th_L3", "volume", "frac_1tick"]


def amostra(p, y, regs, ctrl):
    d = p.dropna(subset=[y] + regs + ctrl).copy()
    for c in [y] + regs + ctrl:
        d[c] = _z(d[c])
    return d


def partial(d, y, regs, ctrl):
    yv, X, Xr, ssr = _fit(d, y, regs, ctrl)
    return (ssr(Xr, yv) - ssr(X, yv)) / ssr(Xr, yv)


def coefs(d, y, regs, ctrl, rng):
    yv, X, Xr, ssr = _fit(d, y, regs, ctrl)
    hac = sm.OLS(yv, X).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
    out = []
    for j, nome in enumerate(regs, start=1):
        b, ic, _ = wild_beta(yv, X, d.dia.to_numpy(), j, rng, B=B)
        out.append({"coef": nome, "estimativa": b, "ic95_webb_inf": ic[0], "ic95_webb_sup": ic[1],
                    "ic95_hac_inf": hac.conf_int()[j][0], "ic95_hac_sup": hac.conf_int()[j][1]})
    return out, (ssr(Xr, yv) - ssr(X, yv)) / ssr(Xr, yv), len(d), d.dia.nunique()


def dif_boot(df_f, df_r, rng, regs_r, ctrl_r):
    """IC da diferença (partial R² reação − partial R² frente), pares por sessão."""
    dias = np.array(sorted(set(df_f.dia) & set(df_r.dia)))
    gf = {k: v for k, v in df_f.groupby("dia")}
    gr = {k: v for k, v in df_r.groupby("dia")}
    vals = []
    for _ in range(B):
        s = rng.choice(dias, len(dias), replace=True)
        f = amostra(pd.concat([gf[k].assign(dia=f"{k}#{i}") for i, k in enumerate(s)]), "y_lead", FR_REGS, FR_CTRL)
        r = amostra(pd.concat([gr[k].assign(dia=f"{k}#{i}") for i, k in enumerate(s)]), "th", regs_r, ctrl_r)
        vals.append(partial(r, "th", regs_r, ctrl_r) - partial(f, "y_lead", FR_REGS, FR_CTRL))
    return np.percentile(vals, [2.5, 97.5])


def main():
    rng = np.random.default_rng(SEMENTE)
    dias = pd.read_csv(PILOTO / "passo3_info_por_dia.csv").dia.tolist()
    ng = pd.concat([negocios_com_pi(d, "principal") for d in dias], ignore_index=True)
    e3 = pd.read_csv(SAIDA / "robustez_E3_theta_kappa.csv")
    linhas = []
    for L in TAMANHOS:
        ngL = atribuir(ng, L)
        P = pd.concat([painel(d, L, ngL) for d in dias], ignore_index=True)
        P.to_parquet(SAIDA / f"rodada3/painel_E4_{L}min.parquet", index=False)
        for h in HS3:
            # ---- principal: θ (dois lados)
            p = preparar(P, L, h, "theta", False)
            regs_r, ctrl_r = re_spec(False)
            dfw = p.dropna(subset=["y_lead"] + FR_REGS + FR_CTRL)
            drw = p.dropna(subset=["th"] + regs_r + ctrl_r)
            cf, pr_f, nf, gf = coefs(amostra(p, "y_lead", FR_REGS, FR_CTRL), "y_lead", FR_REGS, FR_CTRL, rng)
            cr, pr_r, nr, gr = coefs(amostra(p, "th", regs_r, ctrl_r), "th", regs_r, ctrl_r, rng)
            ic_d = dif_boot(dfw, drw, rng, regs_r, ctrl_r)
            beta_e3 = e3[(e3.bloco_min == L) & (e3.h_s == h) & (e3.regressor == "θ_t")]
            comum = {"bloco_min": L, "h_s": h, "theta": "θ (bid+ask)"}
            for c in cf:
                c["coef"] = "β θ_t"
                linhas.append({**comum, "equacao": "para frente", "N_blocos": nf, "n_sessoes": gf, **c,
                               "partial_R2": pr_f, "partial_R2_reacao": pr_r, "dif_reacao_menos_frente": pr_r - pr_f,
                               "dif_ic95_inf": ic_d[0], "dif_ic95_sup": ic_d[1],
                               "beta_E3_mesma_celula": float(beta_e3.beta.iloc[0]) if len(beta_e3) else np.nan,
                               "beta_E3_ic95_webb": (f"[{beta_e3.ic95_webb_inf.iloc[0]:.3f}, {beta_e3.ic95_webb_sup.iloc[0]:.3f}]"
                                                     if len(beta_e3) else "")})
            nomes = {"abs_r": "λ0 |r_t|", "abs_r_L1": "λ1 |r_{t−1}|", "AS_L1": "λ2 AS_{t−1}", "qvol_L1": "λ3 qvol_{t−1}"}
            for c in cr:
                linhas.append({**comum, "equacao": "reação", "N_blocos": nr, "n_sessoes": gr, **c,
                               "coef": nomes[c["coef"]], "partial_R2": pr_r})
            # ---- secundário: por lado, r com sinal
            for col, rot in (("theta_B", "θ^B (bid)"), ("theta_A", "θ^A (ask)")):
                p2 = preparar(P, L, h, col, True)
                regs_s, ctrl_s = re_spec(True)
                cs, pr_s, ns, gs = coefs(amostra(p2, "th", regs_s, ctrl_s), "th", regs_s, ctrl_s, rng)
                nomes_s = {"r": "λ0 r_t", "r_L1": "λ1 r_{t−1}", "AS_L1": "λ2 AS_{t−1}", "qvol_L1": "λ3 qvol_{t−1}"}
                for c in cs:
                    linhas.append({"bloco_min": L, "h_s": h, "theta": rot, "equacao": "reação (secundário, r com sinal)",
                                   "N_blocos": ns, "n_sessoes": gs, **c, "coef": nomes_s[c["coef"]], "partial_R2": pr_s})
    out = pd.DataFrame(linhas)
    out.insert(0, "id", "E4")
    out.to_csv(SAIDA / "causalidade_reversa_theta.csv", index=False)
    return out


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 300)
    print(main().round(4).to_string(index=False))
