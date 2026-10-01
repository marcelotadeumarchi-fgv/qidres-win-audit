"""
Passo 6: QIDres intradiária (5 min) e comparação com as medidas usadas antes.

QIDres intradiária (especificação, "Versão intradiária"; artigo, nota 21):
  para cada dia de interesse d, dia de controle c = 5 pregões antes.
  * intra_spec : passo 1 com os blocos de c E de d (como o artigo), efeitos
                 fixos de HORA do dia (I11: FE por bloco de 5 min saturaria o
                 modelo -- ~106 parâmetros para ~212 obs). S(QID) = DP dos QIDs
                 usados no passo 1. ATENÇÃO: usa o próprio dia d no passo 1 --
                 conflita com a regra 5 do prompt ("sem leitura de futuro");
                 segue-se a especificação e o conflito é registrado.
  * intra_oos  : variante estritamente fora da amostra -- passo 1 e S(QID) só
                 do dia de controle c.
Comparações (5 min, principal):
  * `qid_res` da tese (tese_apendice_a.py), como foi rodada (UTC -> BRT).
  * `qid_signed` (H1 do protocolo), agregado a 5 min por protocolo_estimar.agregar.
Comparação horária (secundária): médias horárias das duas séries × QIDres horária.

Uso: python -m auditoria_qidres.passo6_comparacao
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from scipy import stats

from .passo4_diagnosticos import B, SEMENTE
from .regressao import _desenho, _ols

SAIDA = Path(__file__).parent / "saida"
QID_DIR = Path(__file__).resolve().parent.parent
VERSOES = ["qid_R", "qid_A", "qid_U", "bqid_R", "aqid_R"]
DEFASAGEM_CONTROLE = 5


def qidres_intradiaria(q5: pd.DataFrame) -> pd.DataFrame:
    q5 = q5.copy()
    q5["hora"] = q5.bloco_ini_ms // 3_600_000
    q5["ln_pct_spread"] = np.log(q5.pct_spread_tw)
    dias = sorted(q5.dia.unique())
    out = []
    for i, d in enumerate(dias):
        if i < DEFASAGEM_CONTROLE:
            continue
        c = dias[i - DEFASAGEM_CONTROLE]
        assert c < d
        alvo = q5[q5.dia == d]
        for y in VERSOES:
            for modo, est in (("intra_spec", q5[q5.dia.isin([c, d])]), ("intra_oos", q5[q5.dia == c])):
                est = est.dropna(subset=[y, "ln_pct_spread"])
                if modo == "intra_oos":
                    assert (est.dia < d).all(), "variante fora da amostra usou o próprio dia"
                X, nomes, niveis = _desenho(est, ["ln_pct_spread"], "hora", None)
                fit = _ols(est[y].to_numpy(float), X)
                s = float(est[y].std(ddof=1))
                r = alvo[["dia", "bloco_ini", "bloco_ini_ms", "hora"]].copy()
                r["versao"], r["modo"], r["dia_controle"] = y, modo, c
                r["N_passo1"], r["S_QID"] = len(est), s
                if fit["status"] != "ok" or not s > 0:
                    r["qidres"], r["status"] = np.nan, "nao_identificada"
                else:
                    ok = alvo.hora.isin(niveis)
                    Xa, _, _ = _desenho(alvo, ["ln_pct_spread"], "hora", niveis)
                    fv = fit["beta"][0] + Xa @ fit["beta"][1:]
                    r["qidres"] = np.where(ok, -(alvo[y].to_numpy() - fv) / s, np.nan)
                    r["status"] = np.where(ok, "ok", "nao_identificada")
                    r["b_ln_pct_spread"], r["R2_passo1"] = fit["beta"][1], fit["r2"]
                out.append(r)
    return pd.DataFrame(out if not out else pd.concat(out, ignore_index=True))


def series_anteriores() -> tuple[pd.DataFrame, pd.DataFrame]:
    tese = pl.read_parquet(QID_DIR / "output_data/tese_apendice_a/qid_res_5m_FULL_MONTH_200001274203.parquet")
    tese = (tese.with_columns(brt=pl.col("window_5m") - pl.duration(hours=3))
            .with_columns(dia=pl.col("brt").dt.date().cast(pl.String),
                          ms=(pl.col("brt").dt.hour() * 3_600_000 + pl.col("brt").dt.minute() * 60_000))
            .select("dia", "ms", pl.col("qid_res").alias("tese_qid_res")).to_pandas())
    sys.path.insert(0, str(QID_DIR))
    import protocolo_estimar as pe  # noqa: E402
    g = pe.agregar(pe.carregar(incluir_holdout=True), 5)
    sig = (g.with_columns(dia=pl.col("w").dt.date().cast(pl.String),
                          ms=(pl.col("w").dt.hour() * 3_600_000 + pl.col("w").dt.minute() * 60_000))
           .select("dia", "ms", "qid_signed").to_pandas())
    return tese, sig


def series_fila() -> pd.DataFrame:
    """Medidas de fila de H2/P2a (spec_hipoteses.carregar), com as MESMAS definições
    (assinadas: bid − ask), agregadas a 5 min como razão de somas do painel de 1 s."""
    d = pl.read_parquet(QID_DIR / "output_data/spec_fila/painel_200001274203.parquet")
    d = d.with_columns(brt=pl.col("window_1m") - pl.duration(hours=3))
    d = d.with_columns(hh=pl.col("brt").dt.hour() + pl.col("brt").dt.minute() / 60.0)
    d = d.filter((pl.col("hh") >= 9.0) & (pl.col("hh") <= 18.0))      # mesma sessão de spec_hipoteses
    d = d.with_columns(w=pl.col("brt").dt.truncate("5m"))
    cols = ["fq_wd_bid", "fq_wd_ask", "bq_wd_bid", "bq_wd_ask", "touch_wd_vol_bid", "touch_wd_vol_ask",
            "qcr_len_bid", "qcr_len_ask"]
    g = d.group_by("w").agg([pl.col(c).sum() for c in cols])
    wd = pl.col("touch_wd_vol_bid") + pl.col("touch_wd_vol_ask")
    g = g.with_columns(
        fqcr=pl.when(wd > 0).then((pl.col("fq_wd_bid") - pl.col("fq_wd_ask")) / wd),
        bqcr=pl.when(wd > 0).then((pl.col("bq_wd_bid") - pl.col("bq_wd_ask")) / wd),
        agg_cancel=pl.when(wd > 0).then((pl.col("touch_wd_vol_bid") - pl.col("touch_wd_vol_ask")) / wd),
        qcr=pl.when(wd > 0).then((pl.col("qcr_len_bid") - pl.col("qcr_len_ask")) / wd * 100),
        dia=pl.col("w").dt.date().cast(pl.String),
        ms=pl.col("w").dt.hour() * 3_600_000 + pl.col("w").dt.minute() * 60_000)
    return g.select("dia", "ms", "fqcr", "bqcr", "agg_cancel", "qcr").to_pandas()


def correlacao(df: pd.DataFrame, a: str, b: str, rng) -> dict:
    d = df[["dia", a, b]].replace([np.inf, -np.inf], np.nan).dropna()
    n = len(d)
    if n < 5 or d[a].std() == 0 or d[b].std() == 0:
        return {"N": n, "pearson": np.nan, "spearman": np.nan, "ic95_inf": np.nan, "ic95_sup": np.nan,
                "status": "nao_identificada"}
    rp = float(np.corrcoef(d[a], d[b])[0, 1])
    rs = float(stats.spearmanr(d[a], d[b]).statistic)
    dias = d.dia.unique()
    grp = {k: v for k, v in d.groupby("dia")}
    bs = []
    for _ in range(B):
        x = pd.concat([grp[k] for k in rng.choice(dias, len(dias), replace=True)])
        if x[a].std() > 0 and x[b].std() > 0:
            bs.append(np.corrcoef(x[a], x[b])[0, 1])
    lo, hi = (np.percentile(bs, [2.5, 97.5]) if len(bs) > B * 0.9 else (np.nan, np.nan))
    return {"N": n, "n_dias": len(dias), "pearson": rp, "spearman": rs, "ic95_inf": float(lo),
            "ic95_sup": float(hi), "status": "ok"}


def main():
    rng = np.random.default_rng(SEMENTE)
    q5 = pd.read_parquet(SAIDA / "qid_5min.parquet")
    intra = qidres_intradiaria(q5)
    intra.to_parquet(SAIDA / "qidres_5min_intradiaria.parquet", index=False)

    tese, sig = series_anteriores()
    fila = series_fila()
    ANT = [(tese, "tese_qid_res", "qid_res da tese"), (sig, "qid_signed", "qid_signed / H1"),
           (fila, "fqcr", "FQCR / H2, P2a"), (fila, "bqcr", "BQCR / H2"),
           (fila, "agg_cancel", "AggCancel / H2"), (fila, "qcr", "QCR / spec 2.4")]
    linhas = []
    # ---- principal: 5 min
    for (v, modo), g in intra.groupby(["versao", "modo"]):
        g = g.rename(columns={"bloco_ini_ms": "ms"})
        for df_ant, outra, rot in ANT:
            m = g.merge(df_ant[["dia", "ms", outra]], on=["dia", "ms"], how="left")
            rot = rot + " (5 min)"
            r = correlacao(m, "qidres", outra, rng)
            r.update({"nivel": "5min", "qidres_auditoria": f"{v} [{modo}]", "medida_anterior": rot,
                      "obs": "U fracamente identificada" if v == "qid_U" else ""})
            linhas.append(r)
    # ---- secundária: hora
    qh = pd.read_parquet(SAIDA / "qidres_hourly.parquet")
    qh = qh[qh.versao.str.endswith("|base")]
    qh["dia"] = qh.hora_ts.dt.strftime("%Y-%m-%d")
    qh["h"] = qh.hora_ts.dt.hour
    for df_ant, col, rot in ANT:
        rot = rot + " (média horária)"
        hm = df_ant.assign(h=df_ant.ms // 3_600_000).groupby(["dia", "h"], as_index=False)[col].mean()
        for v, g in qh.groupby("versao"):
            m = g.merge(hm, on=["dia", "h"], how="left")
            r = correlacao(m, "qidres", col, rng)
            r.update({"nivel": "hora", "qidres_auditoria": v, "medida_anterior": rot,
                      "obs": "U fracamente identificada" if v.startswith("qid_U") else ""})
            linhas.append(r)
    comp = pd.DataFrame(linhas)[["nivel", "qidres_auditoria", "medida_anterior", "N", "n_dias", "pearson",
                                 "ic95_inf", "ic95_sup", "spearman", "status", "obs"]]
    comp.to_csv(SAIDA / "comparacao_medidas_anteriores.csv", index=False)
    resumo_intra = (intra.groupby(["versao", "modo"])
                    .agg(N=("qidres", lambda s: s.notna().sum()), media=("qidres", "mean"), dp=("qidres", "std"),
                         n_dias=("dia", "nunique"))
                    .reset_index())
    resumo_intra.to_csv(SAIDA / "qidres_5min_intradiaria_resumo.csv", index=False)
    return comp, resumo_intra


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 100)
    c, r = main()
    print(r.to_string(index=False)); print(c.to_string(index=False))
