"""
Passo 4 da especificação: diagnósticos D2–D7 (não dependem da regressão).
Grava/atualiza saida/audit_diagnostics.csv: valor, IC 95%, N, referência do
artigo, limiar e se cruzou o limiar. Sem veredictos interpretativos.

ICs: bootstrap por dia (reamostra pregões inteiros), B = 999          # rodada 2 (piloto: 2000), semente fixa.
Com 12 pregões o IC é largo; células com variância zero ou N insuficiente são
marcadas "não identificada".

Uso: python -m auditoria_qidres.passo4_diagnosticos
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

SAIDA = Path(__file__).parent / "saida"
B = 999          # rodada 2 (piloto: 2000)
SEMENTE = 20261001

# Limiares operacionalizados a partir da especificação (PENDENTES de confirmação
# do usuário; ver run_log). Onde a especificação diz só "perto de"/"forte",
# o número abaixo é a proposta.
LIMIARES = {
    "D2": "R² > 0,80",
    "D3": "undercut < 10% das melhorias (W = 1 s)",
    "D4": "frac_1tick médio > 0,95",
    "D5": "share_1tick_impr ≥ 0,95 E share_1tick_det_trade ≥ 0,95",
    "D6": "|média do QID^R| < 0,10 OU > 10% dos períodos com QID^R < 0",
    "D7": "concordância < 90%",
}


def _r2(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return np.nan, np.nan, len(x)
    b = np.polyfit(x, y, 1)
    res = y - np.polyval(b, x)
    return 1 - res.var() / y.var(), b[0], len(x)


def boot_por_dia(df: pd.DataFrame, f, rng) -> tuple[float, float]:
    dias = df.dia.unique()
    grupos = {d: g for d, g in df.groupby("dia")}
    vals = []
    for _ in range(B):
        amostra = pd.concat([grupos[d] for d in rng.choice(dias, len(dias), replace=True)])
        v = f(amostra)
        if np.isfinite(v):
            vals.append(v)
    if len(vals) < B * 0.9:
        return np.nan, np.nan
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def boot_razao(num: pd.Series, den: pd.Series, rng) -> tuple[float, float]:
    """IC por bootstrap de pregões para Σnum/Σden (num, den indexados por dia)."""
    n, d = num.to_numpy(float), den.to_numpy(float)
    idx = rng.integers(0, len(n), size=(B, len(n)))
    r = n[idx].sum(1) / d[idx].sum(1)
    return float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def linha(id_, nome, nivel, valor, ic, n, ref, alerta, obs=""):
    ident = np.isfinite(valor) if isinstance(valor, float) else True
    return {"id": id_, "diagnostico": nome, "nivel": nivel,
            "valor": valor if ident else "não identificada",
            "ic95_inf": ic[0], "ic95_sup": ic[1], "N": n, "referencia_artigo": ref,
            "limiar": LIMIARES.get(id_, ""), "cruzou_limiar": (bool(alerta) if ident else "não identificada"),
            "obs": obs}


def main():
    rng = np.random.default_rng(SEMENTE)
    qd = pd.read_parquet(SAIDA / "qid_daily.parquet")
    q5 = pd.read_parquet(SAIDA / "qid_5min.parquet")
    ev = pd.read_parquet(SAIDA / "events_classified.parquet")
    L = []

    # ---- D2: QID^R em (1-θ)/(1+θ)
    for nivel, df in (("diario", qd), ("5min", q5)):
        for y in ("qid_R", "qid_A"):
            r2, b, n = _r2(df.qid_composicao, df[y])
            ic = boot_por_dia(df, lambda a: _r2(a.qid_composicao, a[y])[0], rng) if nivel == "5min" else (np.nan, np.nan)
            obs = "especificação: QID^R" if y == "qid_R" else "adicional: QID^A (onde a identidade é exata se #Impr=#Deter)"
            if nivel == "diario":
                obs += "; N=12 pregões, IC por bootstrap não informativo (omitido)"
            L.append(linha("D2", f"R² de {y} em (1−θ)/(1+θ)", nivel, r2, ic, n, "Não reportado",
                           np.isfinite(r2) and r2 > 0.8, obs + f"; inclinação={b:.3f}" if np.isfinite(b) else obs))
        razao = (df.n_impr / df.n_det_all)
        L.append(linha("D2", "#Impr / #Deter (premissa da identidade)", nivel, float(razao.mean()),
                       boot_por_dia(df.assign(_r=razao), lambda a: a._r.mean(), rng), len(df), "Não reportado",
                       False, "descritivo; sem limiar"))

    # ---- D3: composição das melhorias por W (agregado da amostra)
    imp = ev[ev.tipo == "melhoria"]
    for w in (100, 1000, 5000):
        for classe in ("undercut", "recomposicao", "deslocamento"):
            col = f"classe_w{w}"
            frac = float((imp[col] == classe).mean())
            ic = boot_razao((imp[col] == classe).groupby(imp.dia).sum(), imp.groupby("dia").size(), rng)
            alerta = (classe == "undercut" and w == 1000 and frac < 0.10)
            L.append(linha("D3", f"fração {classe}, W={w} ms", "amostra", frac, ic, len(imp), "Não reportado",
                           alerta, "limiar aplicado só a undercut com W=1 s" if classe == "undercut" else "descritivo"))

    # ---- D4: restrição de tick
    for nivel, df in (("diario", qd), ("5min", q5)):
        for v in ("frac_1tick", "spread_tw_ticks"):
            m = float(df[v].mean())
            ic = boot_por_dia(df, lambda a: a[v].mean(), rng)
            p = df[v].quantile([0.05, 0.5, 0.95]).round(4).tolist()
            L.append(linha("D4", f"{v} (média)", nivel, m, ic, len(df),
                           "Corridas em ações restritas começam com spread ~2¢ (Fig. 3)",
                           v == "frac_1tick" and m > 0.95, f"p5/p50/p95 = {p}"))

    # ---- D5: variações de um tick (diário, médias entre dias)
    for v, ref in (("share_1tick_impr", "0,80 (Tabela 1, NBB)"), ("share_1tick_det_trade", "0,59 (Tabela 1, NBB)")):
        m = float(qd[v].mean())
        L.append(linha("D5", f"{v} (média diária)", "diario", m, boot_por_dia(qd, lambda a: a[v].mean(), rng),
                       len(qd), ref, bool((qd.share_1tick_impr.mean() >= 0.95) and (qd.share_1tick_det_trade.mean() >= 0.95)),
                       "alerta exige as duas ≥ 0,95"))

    # ---- D6: distribuição do QID
    for nivel, df in (("diario", qd), ("5min", q5)):
        for y in ("qid_R", "qid_A", "qid_U"):
            s = df[y].dropna()
            neg = float((s < 0).mean())
            alerta = (abs(s.mean()) < 0.10 or neg > 0.10) if y == "qid_R" else False
            obs = f"mediana={s.median():.4f}; % negativos={100 * neg:.2f}%; ausentes={int(df[y].isna().sum())}"
            L.append(linha("D6", f"média de {y}", nivel, float(s.mean()),
                           boot_por_dia(df, lambda a: a[y].mean(), rng), len(s),
                           "Média 0,61; mediana 0,63; 0,05% negativos (Tabela 1)" if y == "qid_R" else "Não se aplica",
                           alerta, obs + ("" if y == "qid_R" else "; limiar só para QID^R")))

    # ---- D7: atribuição R contra A
    det = ev[ev.tipo == "deterioracao"].copy()
    det["a_exec"] = det.causa_A == "execucao"
    for col, rot in (("r_10ms", "R 10 ms"), ("r_semrlp_10ms", "R 10 ms sem RLP"), ("r_1ms", "R 1 ms")):
        conc = float((det[col] == det.a_exec).mean())
        tab = pd.crosstab(det[col], det.a_exec)
        L.append(linha("D7", f"concordância {rot} × A (execução)", "amostra", conc,
                       boot_razao((det[col] == det.a_exec).groupby(det.dia).sum(), det.groupby("dia").size(), rng),
                       len(det), "Mediana de 5 ms entre negócio e deterioração (seção 3.1)", conc < 0.90,
                       "tabela [R; A]: " + json.dumps({f"R={i},A={j}": int(tab.loc[i, j]) for i in tab.index for j in tab.columns})))

    out = pd.DataFrame(L)
    arq = SAIDA / "audit_diagnostics.csv"
    if arq.exists():
        ant = pd.read_csv(arq)
        out = pd.concat([ant[~ant.id.isin(out.id.unique())], out], ignore_index=True)
    out.to_csv(arq, index=False)
    return out


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 70)
    print(main().drop(columns=["obs"]).to_string(index=False))
