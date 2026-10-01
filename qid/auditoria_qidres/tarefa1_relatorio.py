"""
Rodada 2 — Tarefa 1: relatório da auditoria na amostra completa (os mesmos 12
pregões do piloto; não há mais dados), lado a lado com o piloto.

Saídas:
  saida/auditoria_amostra_completa.csv   D1–D11, amostra × piloto
  saida/distribuicao_bloco_dia.csv       frac_1tick, share_1tick_*, m_I, m_D por bloco e por dia
  relatorio.md                            seção 4 (e cabeçalho da rodada 2)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .passo4_diagnosticos import B, SEMENTE, boot_por_dia

AQ = Path(__file__).parent
SAIDA, PILOTO = AQ / "saida", AQ / "saida_piloto"
CHAVE = ["id", "diagnostico", "nivel"]
VARS = ["frac_1tick", "share_1tick_impr", "share_1tick_det_trade", "m_I", "m_D"]


def _d1(pasta: Path) -> pd.DataFrame:
    qd, q5 = pd.read_parquet(pasta / "qid_daily.parquet"), pd.read_parquet(pasta / "qid_5min.parquet")
    out = []
    for nivel, df in (("diario", qd), ("5min", q5)):
        n_ruim = int((df.d1_residuo != 0).sum())
        out.append({"id": "D1", "diagnostico": "períodos com resíduo da identidade ≠ 0", "nivel": nivel,
                    "valor": n_ruim, "ic95_inf": np.nan, "ic95_sup": np.nan, "N": len(df),
                    "referencia_artigo": "Não reportado", "limiar": "qualquer resíduo ≠ 0",
                    "cruzou_limiar": n_ruim > 0})
    return pd.DataFrame(out)


def _com_m(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "m_I" not in df:
        df["m_I"] = np.where(df.n_impr > 0, df.soma_ticks_impr / df.n_impr.replace(0, np.nan), np.nan)
        df["m_D"] = np.where(df.n_det_all > 0, df.soma_ticks_det / df.n_det_all.replace(0, np.nan), np.nan)
    return df


def distribuicao(pasta: Path, rotulo: str, rng) -> pd.DataFrame:
    linhas = []
    for nivel, arq in (("dia", "qid_daily.parquet"), ("bloco 5 min", "qid_5min.parquet")):
        df = _com_m(pd.read_parquet(pasta / arq))
        for v in VARS:
            s = df[v]
            ok = s.dropna()
            lo, hi = boot_por_dia(df[["dia", v]].dropna(), lambda a: a[v].mean(), rng) if len(ok) else (np.nan, np.nan)
            linhas.append({"amostra": rotulo, "nivel": nivel, "variavel": v, "N": len(ok),
                           "n_nao_identificada": int(s.isna().sum()),
                           "media": ok.mean(), "ic95_inf": lo, "ic95_sup": hi,
                           "p5": ok.quantile(.05), "p50": ok.median(), "p95": ok.quantile(.95),
                           "dp": ok.std()})
    return pd.DataFrame(linhas)


def main():
    rng = np.random.default_rng(SEMENTE)
    novo = pd.concat([_d1(SAIDA), pd.read_csv(SAIDA / "audit_diagnostics.csv")], ignore_index=True)
    pil = pd.concat([_d1(PILOTO), pd.read_csv(PILOTO / "audit_diagnostics.csv")], ignore_index=True)
    cols = ["valor", "ic95_inf", "ic95_sup", "N", "cruzou_limiar"]
    lado = novo.merge(pil[CHAVE + cols], on=CHAVE, how="outer", suffixes=("", "_piloto"))
    lado = lado[CHAVE + cols + [c + "_piloto" for c in cols] + ["referencia_artigo", "limiar", "obs"]]
    lado.to_csv(SAIDA / "auditoria_amostra_completa.csv", index=False)

    dist = pd.concat([distribuicao(SAIDA, "amostra completa", rng), distribuicao(PILOTO, "piloto", rng)])
    dist.to_csv(SAIDA / "distribuicao_bloco_dia.csv", index=False)

    # ---- cabeçalho
    qd = pd.read_parquet(SAIDA / "qid_daily.parquet")
    q5 = pd.read_parquet(SAIDA / "qid_5min.parquet")
    trims = sorted(pd.to_datetime(qd.dia).dt.to_period("Q").astype(str).unique())
    res = json.loads((SAIDA / "passo3_resumo.json").read_text())
    cab = {"pregoes": len(qd), "trimestres": len(trims), "lista_trimestres": trims, "blocos_5min": len(q5),
           "eventos": res["eventos"], "qidres_diaria_calculavel": len(trims) >= 2}

    # mudanças em relação ao piloto (valor pontual ou cruzamento de limiar)
    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return np.nan
    mud = lado[(lado.valor.map(num).round(6) != lado.valor_piloto.map(num).round(6))
               | (lado.cruzou_limiar.astype(str) != lado.cruzou_limiar_piloto.astype(str))]

    f = lambda x: "" if pd.isna(num(x)) else f"{num(x):.4g}"
    t1 = lado.copy()
    for c in ["valor", "ic95_inf", "ic95_sup", "valor_piloto", "ic95_inf_piloto", "ic95_sup_piloto"]:
        t1[c] = t1[c].map(lambda x: f(x) if not isinstance(x, str) or num(x) == num(x) else x)
    base = t1[~t1.id.isin(["D8", "D9"]) | t1.diagnostico.str.contains(r"\|base\]|1ms\]", regex=True)]
    base = base[~((base.id == "D8") & ~base.diagnostico.str.contains(r"qid_R\|base|qid_A\|base|qid_U\|base", regex=True))]
    tab_d = base[["id", "diagnostico", "nivel", "valor", "ic95_inf", "ic95_sup", "N", "cruzou_limiar",
                  "valor_piloto", "ic95_inf_piloto", "ic95_sup_piloto", "cruzou_limiar_piloto"]].to_markdown(index=False)
    td = dist.copy()
    for c in ["media", "ic95_inf", "ic95_sup", "p5", "p50", "p95", "dp"]:
        td[c] = td[c].map(lambda x: f"{x:.4f}")
    tab_dist = td.to_markdown(index=False)

    sec = f"""## 4. Rodada 2 — Tarefa 1: amostra completa

**Pregões: {cab['pregoes']} · trimestres: {cab['trimestres']} ({', '.join(trims)}) · blocos de 5 min: {cab['blocos_5min']} · eventos classificados: {cab['eventos']:,}.**
**QIDres diária (exige trimestre anterior): {'calculável' if cab['qidres_diaria_calculavel'] else 'NÃO calculável — só há um trimestre'}.**

A "amostra completa" é a MESMA do piloto: não há outros pregões em `/share/fgv-quant/market-data/parquet/` (só 09/2025). Esta execução não é confirmatória; repete o pipeline do zero com o código final (hashes no `run_log.md`) e B = 999 (piloto: B = 2.000). Linhas cujo valor pontual ou cruzamento de limiar mudou em relação ao piloto: **{len(mud)}** (ver `saida/auditoria_amostra_completa.csv`).

### 4.1 D1–D11, amostra × piloto (IC 95%, bootstrap por pregão)

Tabela completa (todas as versões e variantes): `saida/auditoria_amostra_completa.csv`.

{tab_d}

### 4.2 Distribuições por bloco e por dia

m_I e m_D: tamanho médio, em ticks, das melhorias e das deteriorações (todas as causas). IC da média por bootstrap por pregão (B = {B}). "n_nao_identificada": períodos sem melhoria (m_I) ou sem deterioração (m_D) ou sem negócio (share_1tick_det_trade).

{tab_dist}
"""
    rel = (AQ / "relatorio.md").read_text()
    rel = re.sub(r"\n## 4\. Rodada 2 — Tarefa 1.*?(?=\n## \d+\. |\Z)", "\n", rel, flags=re.S)
    rel = rel.replace("\n## 4. Dúvidas e desvios", "\n## 7. Dúvidas e desvios")
    rel = rel.replace("\n## 7. Dúvidas e desvios", "\n" + sec + "\n## 7. Dúvidas e desvios", 1)
    topo = (f"> **Rodada 2 (Tarefa 1):** {cab['pregoes']} pregões, {cab['trimestres']} trimestre(s), "
            f"{cab['blocos_5min']} blocos de 5 min; QIDres diária "
            f"{'calculável' if cab['qidres_diaria_calculavel'] else 'não calculável'}; "
            f"mesma amostra do piloto (não confirmatória). Mudanças vs. piloto: {len(mud)} linhas.\n")
    rel = re.sub(r"> \*\*Rodada 2 \(Tarefa 1\):\*\*.*\n", "", rel)
    rel = rel.replace("\n## ⚠ Resultado contrário à hipótese registrada", "\n" + topo + "\n## ⚠ Resultado contrário à hipótese registrada", 1)
    (AQ / "relatorio.md").write_text(rel)
    (SAIDA / "tarefa1_cabecalho.json").write_text(json.dumps({**cab, "linhas_mudaram_vs_piloto": len(mud)}, indent=2))
    print(json.dumps({**cab, "linhas_mudaram_vs_piloto": len(mud)}, indent=2))
    if len(mud):
        print(mud[CHAVE + ["valor", "valor_piloto", "cruzou_limiar", "cruzou_limiar_piloto"]].to_string(index=False))


if __name__ == "__main__":
    main()
