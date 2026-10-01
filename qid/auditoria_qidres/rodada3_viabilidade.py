"""
Rodada 3 — Teste de viabilidade: existe seleção adversa variando entre blocos de
5 min no WIN, e as medidas de undercutting a acompanham?

Passo 1 (confirmatório): seleção adversa por bloco e sua confiabilidade.
  Negócio = um evento de casamento com negócio. d = sinal exato pelo lado
  passivo consumido (principal; eventos só-RLP excluídos) ou, na robustez,
  eventos só-RLP com sinal pela regra de cotação (p vs m_{t⁻}).
  m_{t⁻}: midpoint do último estado válido antes do evento. p: VWAP do evento.
  PI_h = d·(m_{t+h} − m_{t⁻}); ES = d·(p − m_{t⁻}); RS_h = ES − PI_h (ticks).
  Descartado para h se t+h ≥ fim da janela válida, ou se (t, t+h] toca uma
  exclusão/leilão ou um estado de referência.
  AS_{t,h} = média de PI_h ponderada pela quantidade no bloco.
  Confiabilidade: metades par/ímpar pela ordem temporal no bloco; r entre as
  metades ao longo dos blocos; Spearman-Brown 2r/(1+r). IC: wild cluster
  bootstrap por sessão, pesos de Webb (6 pontos), B = 9.999: AS_par = a + b·AS_ímpar
  + u; AS_par* = â + b̂·AS_ímpar + w_g·û; SB* calculado com AS_par*.
  Versões: AS bruto e AS menos a média por hora do dia (cada metade).
Passo 2 (confirmatório; θ e κ exploratórios): AS_{t+1,h} em M_t, AS_{t,h},
  controles (volume, qvol, |r_t|, frac_1tick) e efeitos fixos de hora. Tudo
  padronizado (DP = 1). β: IC por wild cluster bootstrap (Webb, B = 9.999;
  bootstrap não restrito, IC básico β̂ − quantis de (β* − β̂)); HAC (Newey-West,
  5 defasagens) secundário. Partial R² de M. Descritivo: AS_{t,h} em M_t.
Passo 3: poder com o EP do wild bootstrap; sessões necessárias para β = 0,10 e 0,20.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .passo2_um_pregao import BLOCO_MS, blocos_5min, hhmmss
from .pipeline import janela_valida
from .regressao import _desenho, _ols
from .tarefa2_causalidade import blocos_r_ofi

AQ = Path(__file__).parent
SAIDA = AQ / "saida"
R3 = SAIDA / "rodada3"
PILOTO = AQ / "saida_piloto"
HS = (5, 30, 60)
B_WILD = 9999
SEMENTE = 20261003
LIM_VARIA, LIM_QUASE = 0.50, 0.30
WEBB = np.array([-np.sqrt(1.5), -1.0, -np.sqrt(0.5), np.sqrt(0.5), 1.0, np.sqrt(1.5)])


# ---------------------------------------------------------------- Passo 1
def negocios_com_pi(dia: str, amostra: str) -> pd.DataFrame:
    dd = dia.replace("-", "")
    ng = pd.read_parquet(R3 / f"negocios_eventos_{dd}.parquet")
    es = pd.read_parquet(PILOTO / "por_dia" / f"estados_{dd}.parquet", columns=["t_ms", "b", "a", "referencia"])
    ini, fim, exc = janela_valida(dia)
    ng = ng[ng.a_ant > ng.b_ant].copy()
    ng["m_ant"] = (ng.a_ant + ng.b_ant) / 2.0
    if amostra == "principal":
        ng = ng[ng.d != 0]
    else:  # robustez: + eventos só-RLP com sinal pela regra de cotação
        q = np.sign(ng.p_tick - ng.m_ant)
        ng["d"] = np.where((ng.d == 0) & ng.so_rlp, q, ng.d)
        ng = ng[ng.d != 0]
    t_es = es.t_ms.to_numpy()
    mid_es = (es.a.to_numpy() + es.b.to_numpy()) / 2.0
    ref_cum = np.cumsum(es.referencia.to_numpy())
    t = ng.t_ms.to_numpy()
    ng["ES"] = ng.d * (ng.p_tick - ng.m_ant)
    for h in HS:
        th = t + h * 1000
        j = np.searchsorted(t_es, th, "right") - 1
        j0 = np.searchsorted(t_es, t, "right") - 1
        ok = (th < fim) & (j >= 0)
        for a, b in exc:
            ok &= ~((th > a) & (t < b))
        ok &= ref_cum[np.clip(j, 0, None)] == ref_cum[np.clip(j0, 0, None)]   # nenhum estado de referência em (t, t+h]
        pi = ng.d.to_numpy() * (mid_es[np.clip(j, 0, None)] - ng.m_ant.to_numpy())
        ng[f"PI_{h}"] = np.where(ok, pi, np.nan)
        ng[f"RS_{h}"] = ng.ES - ng[f"PI_{h}"]
    blocos = blocos_5min(ini, fim, exc)
    ini_b = np.array([x for x, _ in blocos])
    k = np.searchsorted(ini_b, t, "right") - 1
    dentro = (k >= 0) & (t < ini_b[np.clip(k, 0, None)] + BLOCO_MS)
    ng["bloco_ini_ms"] = np.where(dentro, ini_b[np.clip(k, 0, None)], -1)
    ng = ng[ng.bloco_ini_ms >= 0].sort_values("t_ms")
    ng["dia"] = dia
    return ng


def as_por_bloco(ng: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (dia, b), g in ng.groupby(["dia", "bloco_ini_ms"]):
        lin = {"dia": dia, "bloco_ini_ms": b, "hora": b // 3_600_000, "n_negocios": len(g), "volume": g.vol.sum(),
               "ES_media": np.average(g.ES, weights=g.vol)}
        for h in HS:
            v = g.dropna(subset=[f"PI_{h}"])
            lin[f"n_{h}"] = len(v)
            if len(v) == 0:
                lin.update({f"AS_{h}": np.nan, f"RS_{h}": np.nan, f"AS_{h}_impar": np.nan, f"AS_{h}_par": np.nan})
                continue
            w = v.vol.to_numpy()
            lin[f"AS_{h}"] = np.average(v[f"PI_{h}"], weights=w)
            lin[f"RS_{h}"] = np.average(v[f"RS_{h}"], weights=w)
            imp, par = v.iloc[0::2], v.iloc[1::2]
            lin[f"AS_{h}_impar"] = np.average(imp[f"PI_{h}"], weights=imp.vol) if len(imp) else np.nan
            lin[f"AS_{h}_par"] = np.average(par[f"PI_{h}"], weights=par.vol) if len(par) else np.nan
        out.append(lin)
    return pd.DataFrame(out)


def spearman_brown(r):
    return 2 * r / (1 + r)


def wild_sb(x, y, cl, rng, B=B_WILD):
    """IC do Spearman-Brown por wild cluster bootstrap (Webb) na regressão y = a + b x."""
    X = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    yh, u = X @ beta, y - X @ beta
    grupos, gi = np.unique(cl, return_inverse=True)
    W = rng.choice(WEBB, size=(B, len(grupos)))
    Y = yh[None, :] + W[:, gi] * u[None, :]
    xc = x - x.mean()
    Yc = Y - Y.mean(1, keepdims=True)
    r = (Yc @ xc) / (np.sqrt((Yc ** 2).sum(1)) * np.sqrt((xc ** 2).sum()))
    sb = spearman_brown(r)
    return np.percentile(sb, [2.5, 97.5]), sb


def classif(sb):
    return "varia" if sb >= LIM_VARIA else ("quase não varia" if sb < LIM_QUASE else "inconclusivo")


def frac_var_hora(bl: pd.DataFrame, col: str) -> float:
    d = bl.dropna(subset=[col])
    y = d[col].to_numpy()
    X = pd.get_dummies(d.hora.astype(str), drop_first=True).to_numpy(float)
    X = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    return 1 - res.var() / y.var()


def passo1(rng):
    dias = pd.read_csv(PILOTO / "passo3_info_por_dia.csv").dia.tolist()
    linhas, blocos_all, dist = [], [], []
    for amostra in ("principal", "robustez_rlp_regra_cotacao"):
        ng = pd.concat([negocios_com_pi(d, amostra) for d in dias], ignore_index=True)
        bl = as_por_bloco(ng)
        bl["amostra"] = amostra
        blocos_all.append(bl)
        for h in HS:
            for var, rot in ((f"PI_{h}", "PI"), (f"RS_{h}", "RS")):
                s = ng[var].dropna()
                w = ng.loc[s.index, "vol"]
                dist.append({"amostra": amostra, "h_s": h, "medida": rot, "N_negocios": len(s),
                             "media_pond_qtd": float(np.average(s, weights=w)), "media": s.mean(), "dp": s.std(),
                             "frac_zero": float((s == 0).mean()),
                             **{f"p{q}": float(s.quantile(q / 100)) for q in (1, 5, 25, 50, 75, 95, 99)}})
            for versao in ("bruto", "sem_media_por_hora"):
                d = bl.dropna(subset=[f"AS_{h}_impar", f"AS_{h}_par"]).copy()
                x, y = d[f"AS_{h}_impar"].to_numpy(), d[f"AS_{h}_par"].to_numpy()
                if versao == "sem_media_por_hora":
                    x = x - d.groupby("hora")[f"AS_{h}_impar"].transform("mean").to_numpy()
                    y = y - d.groupby("hora")[f"AS_{h}_par"].transform("mean").to_numpy()
                r = float(np.corrcoef(x, y)[0, 1])
                sb = spearman_brown(r)
                ic, _ = wild_sb(x, y, d.dia.to_numpy(), rng)
                linhas.append({"amostra": amostra, "h_s": h, "versao": versao, "N_blocos": len(d),
                               "n_sessoes": d.dia.nunique(), "r_metades": r, "spearman_brown": sb,
                               "ic95_inf": ic[0], "ic95_sup": ic[1], "classificacao_preregistrada": classif(sb),
                               "frac_var_AS_por_hora": frac_var_hora(bl, f"AS_{h}") if versao == "bruto" else np.nan})
    bl = pd.concat(blocos_all, ignore_index=True)
    bl["bloco_ini"] = bl.bloco_ini_ms.map(hhmmss)
    return pd.DataFrame(linhas), bl, pd.DataFrame(dist)


# ---------------------------------------------------------------- Passo 2
def qidres_oos(q5: pd.DataFrame, defasagem: int, versao: str) -> pd.DataFrame:
    """QIDres intradiária fora da amostra: passo 1 e S(QID) só no dia de controle."""
    q5 = q5.copy()
    q5["hora"] = q5.bloco_ini_ms // 3_600_000
    q5["ln_pct_spread"] = np.log(q5.pct_spread_tw)
    dias = sorted(q5.dia.unique())
    out = []
    for i in range(defasagem, len(dias)):
        d, c = dias[i], dias[i - defasagem]
        est = q5[q5.dia == c].dropna(subset=[versao, "ln_pct_spread"])
        assert (est.dia < d).all()
        alvo = q5[q5.dia == d]
        X, _, niveis = _desenho(est, ["ln_pct_spread"], "hora", None)
        fit = _ols(est[versao].to_numpy(float), X)
        s = float(est[versao].std(ddof=1))
        r = alvo[["dia", "bloco_ini_ms"]].copy()
        if fit["status"] != "ok" or not s > 0:
            r["M"] = np.nan
        else:
            Xa, _, _ = _desenho(alvo, ["ln_pct_spread"], "hora", niveis)
            fv = fit["beta"][0] + Xa @ fit["beta"][1:]
            r["M"] = np.where(alvo.hora.isin(niveis), -(alvo[versao].to_numpy() - fv) / s, np.nan)
        out.append(r)
    return pd.concat(out, ignore_index=True)


def wild_beta(y, X, cl, j, rng, B=B_WILD):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    yh, u = X @ beta, y - X @ beta
    grupos, gi = np.unique(cl, return_inverse=True)
    W = rng.choice(WEBB, size=(B, len(grupos)))
    XtX_inv_Xt = np.linalg.pinv(X)
    bs = (XtX_inv_Xt @ (yh[None, :] + W[:, gi] * u[None, :]).T)[j]
    d = bs - beta[j]
    return beta[j], (beta[j] - np.percentile(d, 97.5), beta[j] - np.percentile(d, 2.5)), float(bs.std(ddof=1))


def _z(s):
    return (s - s.mean()) / s.std(ddof=1)


def regressao(df: pd.DataFrame, ycol: str, ctrl: list[str], rng, contemporanea=False):
    d = df.dropna(subset=[ycol, "M"] + ctrl).copy()
    for c in ["M", ycol] + ctrl:
        d[c] = _z(d[c])
    fe = pd.get_dummies(d.hora.astype(str), drop_first=True).to_numpy(float)
    Xr = np.column_stack([np.ones(len(d)), d[ctrl].to_numpy(float), fe])
    X = np.column_stack([np.ones(len(d)), d["M"].to_numpy(), d[ctrl].to_numpy(float), fe])
    y = d[ycol].to_numpy()
    b, ic, se_wild = wild_beta(y, X, d.dia.to_numpy(), 1, rng)
    hac = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
    ssr = lambda A: float(((y - A @ np.linalg.lstsq(A, y, rcond=None)[0]) ** 2).sum())
    pr2 = (ssr(Xr) - ssr(X)) / ssr(Xr)
    res = y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
    # correlação intraclasse dos resíduos por sessão (ANOVA de um fator)
    g = pd.DataFrame({"g": d.dia.to_numpy(), "e": res})
    k = g.groupby("g").size()
    n0 = (len(g) - (k ** 2).sum() / len(g)) / (len(k) - 1)
    msb = (k * (g.groupby("g").e.mean() - g.e.mean()) ** 2).sum() / (len(k) - 1)
    msw = ((g.e - g.groupby("g").e.transform("mean")) ** 2).sum() / (len(g) - len(k))
    icc = (msb - msw) / (msb + (n0 - 1) * msw)
    return {"beta": b, "ic95_inf": ic[0], "ic95_sup": ic[1], "ep_wild": se_wild,
            "ic95_hac_inf": hac.conf_int()[1][0], "ic95_hac_sup": hac.conf_int()[1][1],
            "partial_R2_M": pr2, "N_blocos": len(d), "n_sessoes": d.dia.nunique(),
            "blocos_por_sessao": len(d) / d.dia.nunique(), "icc_residuo_sessao": float(icc),
            "exclui_zero": bool(ic[0] > 0 or ic[1] < 0)}


def passo2(bl: pd.DataFrame, rng):
    q5 = pd.read_parquet(PILOTO / "qid_5min.parquet")
    q5["m_I"] = q5.soma_ticks_impr / q5.n_impr.replace(0, np.nan)
    q5["m_D"] = q5.soma_ticks_det / q5.n_det_all.replace(0, np.nan)
    q5["theta"] = q5.n_det_exec / q5.n_det_all.replace(0, np.nan)
    q5["kappa"] = q5.m_D / q5.m_I
    dias = sorted(q5.dia.unique())
    rof = pd.concat([blocos_r_ofi(d, pd.read_parquet(PILOTO / "por_dia" / f"estados_{d.replace('-', '')}.parquet"))
                     for d in dias], ignore_index=True).rename(columns={"ms": "bloco_ini_ms"})
    base = (bl[bl.amostra == "principal"]
            .merge(q5[["dia", "bloco_ini_ms", "qvol", "frac_1tick", "theta", "kappa"]], on=["dia", "bloco_ini_ms"])
            .merge(rof[["dia", "bloco_ini_ms", "r"]], on=["dia", "bloco_ini_ms"], how="left"))
    base["abs_r"] = base.r.abs()
    base = base.sort_values(["dia", "bloco_ini_ms"])
    for h in HS:
        sh = base.groupby("dia")[[f"AS_{h}", "bloco_ini_ms"]].shift(-1)
        base[f"AS_{h}_lead"] = np.where(sh.bloco_ini_ms == base.bloco_ini_ms + BLOCO_MS, sh[f"AS_{h}"], np.nan)
    medidas = []
    for defas, rot in ((1, "controle=pregão anterior (principal, D-4)"), (5, "controle=5 pregões antes (artigo)")):
        for v, nome in (("qid_R", "QIDres^R"), ("qid_A", "QIDres^A")):
            medidas.append((rot, nome, "confirmatorio", qidres_oos(q5, defas, v)))
    for v, nome in (("theta", "θ_t (E)"), ("kappa", "κ_t (E)")):
        medidas.append(("n/a", nome, "exploratorio", base[["dia", "bloco_ini_ms", v]].rename(columns={v: "M"})))
    ctrl_base = ["volume", "qvol", "abs_r", "frac_1tick"]
    linhas = []
    for versao, nome, status, med in medidas:
        df = base.drop(columns=[c for c in ("M",) if c in base]).merge(med, on=["dia", "bloco_ini_ms"], how="inner")
        for h in HS:
            for tipo, y, ctrl in (("preditiva (t+1)", f"AS_{h}_lead", ctrl_base + [f"AS_{h}"]),
                                  ("contemporânea (descritiva)", f"AS_{h}", ctrl_base)):
                r = regressao(df, y, ctrl, rng)
                r.update({"medida": nome, "versao_qidres": versao, "status": status, "h_s": h, "tipo": tipo,
                          "sinal_preregistrado": "β > 0" if tipo.startswith("preditiva") else "n/a (descritivo)"})
                linhas.append(r)
    return pd.DataFrame(linhas)


# ---------------------------------------------------------------- Passo 3
def passo3(q: pd.DataFrame) -> pd.DataFrame:
    z = 1.959964 + 0.841621
    p = q[q.tipo.str.startswith("preditiva")].copy()
    p["beta_min_detectavel_80"] = z * p.ep_wild
    for alvo in (0.10, 0.20):
        p[f"sessoes_para_beta_{alvo:.2f}"] = np.ceil(p.n_sessoes * (z * p.ep_wild / alvo) ** 2).astype(int)
    p["efeito_desenho"] = 1 + (p.blocos_por_sessao - 1) * p.icc_residuo_sessao
    return p[["medida", "versao_qidres", "status", "h_s", "n_sessoes", "N_blocos", "blocos_por_sessao",
              "icc_residuo_sessao", "efeito_desenho", "ep_wild", "beta_min_detectavel_80",
              "sessoes_para_beta_0.10", "sessoes_para_beta_0.20"]]


def main():
    rng = np.random.default_rng(SEMENTE)
    conf, bl, dist = passo1(rng)
    bl.to_parquet(SAIDA / "selecao_adversa_blocos.parquet", index=False)
    conf.to_csv(SAIDA / "confiabilidade_AS.csv", index=False)
    dist.to_csv(SAIDA / "distribuicao_PI_RS.csv", index=False)
    p = conf[conf.amostra == "principal"]
    parar = bool((p.spearman_brown < LIM_QUASE).all())
    resumo = {"parada_condicional": parar}
    if not parar:
        q = passo2(bl, rng)
        q.to_csv(SAIDA / "qidres_vs_AS.csv", index=False)
        passo3(q).to_csv(SAIDA / "poder.csv", index=False)
    (R3 / "resumo.json").write_text(json.dumps(resumo, indent=2))
    print(json.dumps(resumo))


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    main()
