"""
Regressão QIDres (especificação, seção "Regressão QIDres"; artigo, eqs. 2-3).

Passo 1: em cada trimestre-calendário q-1, QID = a + b·X + u (X = ln %spread
por padrão; outros controles nas variantes).
Passo 2: em q, QIDres = -(QID - (â_{q-1} + b̂_{q-1}·X)) / S(QID)_{q-1}, com
S(QID) = DP dos QIDs DIÁRIOS observados em q-1 (não dos resíduos).
O primeiro trimestre da amostra só estima.

Toda célula sem identificação (N < k+2, ou controle com variância zero, ou
S(QID) = 0) é marcada "nao_identificada" e não gera QIDres.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

N_MIN_EXTRA = 2   # N mínimo = nº de parâmetros + 2 (pelo menos 1 g.l. de resíduo e DP)


def _ols(y: np.ndarray, X: np.ndarray) -> dict:
    n, k = X.shape
    Xc = np.column_stack([np.ones(n), X])
    if n < k + 1 + N_MIN_EXTRA:
        return {"status": "nao_identificada", "motivo": f"N={n} < {k + 1 + N_MIN_EXTRA}"}
    if np.any(np.nanstd(X, axis=0) == 0):
        return {"status": "nao_identificada", "motivo": "controle com variância zero"}
    beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    res = y - Xc @ beta
    gl = n - k - 1
    s2 = float(res @ res) / gl
    cov = s2 * np.linalg.inv(Xc.T @ Xc)
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(res @ res) / sst if sst > 0 else float("nan")
    return {"status": "ok", "beta": beta, "se": np.sqrt(np.diag(cov)), "r2": r2, "n": n, "gl": gl}


def _desenho(df: pd.DataFrame, xs: list[str], fe: str | None, niveis: list | None):
    """Matriz [xs | dummies de fe (sem a 1ª categoria)]. Devolve (X, nomes, niveis)."""
    X = df[xs].to_numpy(float)
    nomes = list(xs)
    if fe is None:
        return X, nomes, None
    if niveis is None:
        niveis = sorted(df[fe].unique())
    dums = [(df[fe] == v).to_numpy(float) for v in niveis[1:]]
    if dums:
        X = np.column_stack([X] + dums)
    nomes += [f"fe_{fe}_{v}" for v in niveis[1:]]
    return X, nomes, niveis


def qidres_por_janela(df: pd.DataFrame, y: str, xs: list[str], janela_col: str, data_col: str,
                      fe: str | None = None, rotulo: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Eqs. (2)-(3) com janela genérica (trimestre no artigo; semana no desvio D-3).

    Passo 1 na janela j-1 (com efeitos fixos de `fe`, se dado); passo 2 em j.
    S(QID) = DP dos QIDs observados em j-1. A 1ª janela só estima.
    Observação de j cuja categoria de `fe` não existe em j-1 -> nao_identificada.
    """
    d = df.copy()
    d["_t"] = pd.to_datetime(d[data_col])
    jans = sorted(d[janela_col].unique())
    primeiro, saidas = [], []
    for i, j in enumerate(jans):
        est = d[d[janela_col] == j].dropna(subset=[y] + xs)
        X, nomes, niveis = _desenho(est, xs, fe, None)
        fit = _ols(est[y].to_numpy(float), X)
        s_qid = float(np.std(est[y].to_numpy(float), ddof=1)) if len(est) >= 2 else float("nan")
        lin = {"versao": rotulo, "y": y, "controles": "+".join(xs) + (f"+FE({fe})" if fe else ""),
               "janela": j, "N": len(est), "S_QID": s_qid, "status": fit["status"],
               "motivo": fit.get("motivo", ""), "_niveis": niveis, "_t_max": est["_t"].max()}
        if fit["status"] == "ok":
            lin.update({"a": fit["beta"][0], "se_a": fit["se"][0], "R2": fit["r2"]})
            for k, nm in enumerate(nomes):
                lin[f"b_{nm}"] = fit["beta"][k + 1]
                lin[f"se_b_{nm}"] = fit["se"][k + 1]
        primeiro.append(lin)
        if i == 0:
            continue
        prev = primeiro[i - 1]
        alvo = d[d[janela_col] == j].copy()
        alvo["janela_param"] = prev["janela"]
        if prev["status"] != "ok" or not (prev["S_QID"] > 0):
            alvo["qidres"], alvo["status"] = np.nan, "nao_identificada"
        else:
            # sem leitura de futuro: tudo que estimou os parâmetros é anterior à janela aplicada
            assert prev["_t_max"] < alvo["_t"].min(), "parâmetros usam dados da janela aplicada"
            fv = prev["a"] + sum(prev[f"b_{x}"] * alvo[x] for x in xs)
            ok = pd.Series(True, index=alvo.index)
            if fe is not None:
                nv = prev["_niveis"]
                ok = alvo[fe].isin(nv)
                for v in nv[1:]:
                    fv = fv + prev[f"b_fe_{fe}_{v}"] * (alvo[fe] == v)
            alvo["qidres"] = np.where(ok, -(alvo[y] - fv) / prev["S_QID"], np.nan)
            alvo["status"] = np.where(ok, "ok", "nao_identificada")
        saidas.append(alvo[[data_col, janela_col, "janela_param", "qidres", "status"]
                           + ([fe] if fe else [])])
    res = pd.concat(saidas) if saidas else pd.DataFrame()
    res["versao"] = rotulo
    fs = pd.DataFrame(primeiro).drop(columns=["_niveis", "_t_max"])
    return res, fs


def trimestre(d: pd.Series) -> pd.Series:
    return pd.to_datetime(d).dt.to_period("Q").astype(str)


def qidres_trimestral(df: pd.DataFrame, y: str, xs: list[str], data_col: str = "dia",
                      excluir: pd.Series | None = None, rotulo: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retorna (qidres por dia, primeiro estágio por trimestre)."""
    d = df[[data_col, y] + xs].copy()
    d["_data"] = pd.to_datetime(d[data_col])
    d["trimestre"] = trimestre(d[data_col])
    d["_usar_est"] = True if excluir is None else ~excluir.reindex(d.index).fillna(False)
    trims = sorted(d.trimestre.unique())
    primeiro, saidas = [], []
    for i, q in enumerate(trims):
        est = d[(d.trimestre == q) & d._usar_est].dropna(subset=[y] + xs)
        fit = _ols(est[y].to_numpy(float), est[xs].to_numpy(float))
        s_qid = float(np.std(est[y].to_numpy(float), ddof=1)) if len(est) >= 2 else float("nan")
        linha = {"versao": rotulo, "y": y, "controles": "+".join(xs), "trimestre": q,
                 "N": len(est), "S_QID": s_qid, "status": fit["status"],
                 "motivo": fit.get("motivo", "")}
        if fit["status"] == "ok":
            linha.update({"a": fit["beta"][0], "se_a": fit["se"][0], "R2": fit["r2"]})
            for j, x in enumerate(xs):
                linha[f"b_{x}"] = fit["beta"][j + 1]
                linha[f"se_b_{x}"] = fit["se"][j + 1]
        primeiro.append(linha)

        if i == 0:
            continue                        # primeiro trimestre só estima
        prev = primeiro[i - 1]
        alvo = d[d.trimestre == q].copy()
        alvo["trimestre_param"] = prev["trimestre"]
        if prev["status"] != "ok" or not (prev["S_QID"] > 0):
            alvo["qidres"] = np.nan
            alvo["status"] = "nao_identificada"
        else:
            # sem leitura de futuro: tudo que estimou os parâmetros é anterior a q
            datas_est = d[(d.trimestre == prev["trimestre"])]["_data"]
            assert datas_est.max() < alvo["_data"].min(), "parâmetros usam dados do trimestre aplicado"
            fit_v = prev["a"] + sum(prev[f"b_{x}"] * alvo[x] for x in xs)
            alvo["qidres"] = -(alvo[y] - fit_v) / prev["S_QID"]
            alvo["status"] = "ok"
        saidas.append(alvo[[data_col, "trimestre", "trimestre_param", "qidres", "status"]])
    res = pd.concat(saidas) if saidas else pd.DataFrame(
        columns=[data_col, "trimestre", "trimestre_param", "qidres", "status"])
    res["versao"] = rotulo
    return res, pd.DataFrame(primeiro)


def qidres_movel(df: pd.DataFrame, y: str, xs: list[str], janela: int = 60,
                 data_col: str = "dia", rotulo: str = "") -> pd.DataFrame:
    """Variante Rolling: parâmetros e S(QID) dos `janela` pregões ANTERIORES."""
    d = df.sort_values(data_col).reset_index(drop=True)
    out = []
    for i in range(len(d)):
        if i < janela:
            out.append({data_col: d.loc[i, data_col], "qidres": np.nan, "status": "nao_identificada"})
            continue
        est = d.iloc[i - janela:i].dropna(subset=[y] + xs)
        assert pd.to_datetime(est[data_col]).max() < pd.to_datetime(d.loc[i, data_col]), \
            "janela móvel inclui o próprio dia ou o futuro"
        fit = _ols(est[y].to_numpy(float), est[xs].to_numpy(float))
        s_qid = float(np.std(est[y].to_numpy(float), ddof=1))
        if fit["status"] != "ok" or not s_qid > 0:
            out.append({data_col: d.loc[i, data_col], "qidres": np.nan, "status": "nao_identificada"})
            continue
        fv = fit["beta"][0] + sum(fit["beta"][j + 1] * d.loc[i, x] for j, x in enumerate(xs))
        out.append({data_col: d.loc[i, data_col], "qidres": -(d.loc[i, y] - fv) / s_qid, "status": "ok"})
    r = pd.DataFrame(out)
    r["versao"] = rotulo
    return r


def winsorizar(s: pd.Series, p_inf=0.01, p_sup=0.99) -> pd.Series:
    lo, hi = s.quantile(p_inf), s.quantile(p_sup)
    return s.clip(lo, hi)
