"""
Passo 2 da especificação: contagem e agregação sobre UM pregão, com exportação
de 20 eventos de cada classe (estado do book antes e depois + mensagens brutas
do evento) para inspeção manual.

Uso: python -m auditoria_qidres.passo2_um_pregao 2025-09-30
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

from .agregacao import agregar_periodo
from .contagem import W_PADRAO_MS
from .extracao import caminho
from .pipeline import janela_valida, rodar_dia

SAIDA = Path(__file__).parent / "saida" / "passo2"
BLOCO_MS = 5 * 60_000
N_AMOSTRA = 20
SEMENTE = 20261001


def hhmmss(ms: int) -> str:
    ms = int(ms)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def blocos_5min(ini: int, fim: int, exc) -> list[tuple[int, int]]:
    """Grade alinhada ao relógio; só blocos inteiros dentro da janela; descarta os que cruzam exclusão."""
    a = -(-ini // BLOCO_MS) * BLOCO_MS
    out = []
    while a + BLOCO_MS <= fim:
        b = a + BLOCO_MS
        if not any(x < b and a < y for x, y in exc):
            out.append((a, b))
        a = b
    return out


def contexto_melhoria(r, es: pd.DataFrame, w: int) -> dict:
    """Referências usadas na classificação (I1, I2), recalculadas para inspeção."""
    ant = es[es.t_ms <= r.t_ms]
    ant = ant[ant.k < r.k]
    t_fim = np.append(ant.t_ms.to_numpy()[1:], r.t_ms)
    jan = ant[t_fim > r.t_ms - w]
    return {"max_bid_janela_W": int(jan.b.max()) if len(jan) else None,
            "min_ask_janela_W": int(jan.a.min()) if len(jan) else None}


def _negocios_brutos(dia: str) -> pd.DataFrame:
    """Volume por evento no pregão contínuo, refeito do arquivo bruto (para reagregar)."""
    from .pipeline import ms_brt
    d = pl.read_parquet(caminho(dia), columns=["side", "sending_time", "size"]).filter(pl.col("side") == 2)
    return pd.DataFrame({"t_ms": ms_brt(d["sending_time"].to_numpy()), "volume": d["size"].to_numpy()})


def main(dia: str, reagregar: bool = False, atravessa_ms: bool = False):
    """reagregar=True: reaproveita eventos/estados/negócios já salvos (não reconta)."""
    global SAIDA
    if atravessa_ms:
        SAIDA = SAIDA.parent / "passo2_D1b"
    SAIDA.mkdir(parents=True, exist_ok=True)
    dd = dia.replace("-", "")
    if reagregar:
        ev = pd.read_parquet(SAIDA / f"events_classified_{dd}.parquet")
        es = pd.read_parquet(SAIDA / f"estados_{dd}.parquet")
        resumo_ant = json.loads((SAIDA / f"resumo_{dd}.json").read_text())
        base = float(pl.scan_parquet(caminho(dia)).select(pl.col("price").min()).collect().item()) - 50.0
        ng = _negocios_brutos(dia)
        cont = None
    else:
        r = rodar_dia(dia, atravessa_ms=atravessa_ms)
        cont, base = r["contador"], r["base"]
        ev = pd.DataFrame(cont.eventos)
        es = cont.estados_df()
        ng = pd.DataFrame(cont.negocios, columns=["t_ms", "volume"])
        ng.to_parquet(SAIDA / f"negocios_{dd}.parquet", index=False)
    ini, fim, exc = janela_valida(dia)
    assert ev.t_ms.min() >= ini and ev.t_ms.max() < fim, "evento fora da janela válida"
    for a, b in exc:
        assert not ((ev.t_ms >= a) & (ev.t_ms < b)).any(), "evento dentro de exclusão"

    pts = lambda tk: base + tk * 5.0
    ev_out = ev.copy()
    for c in ("b_ant", "a_ant", "b", "a"):
        ev_out[c + "_pts"] = pts(ev_out[c])
    ev_out["hora_brt"] = ev_out.t_ms.map(hhmmss)
    ev_out.to_parquet(SAIDA / f"events_classified_{dd}.parquet", index=False)
    es.to_parquet(SAIDA / f"estados_{dd}.parquet", index=False)

    # ---- agregação: dia e blocos de 5 min
    rot = {"dia": dia}
    lin_dia = agregar_periodo(ev, es, ng, ini, fim, rot, exc, base)
    linhas = []
    for a, b in blocos_5min(ini, fim, exc):
        linhas.append(agregar_periodo(ev, es, ng, a, b, {"dia": dia, "bloco_ini": hhmmss(a)}, exc, base))
    b5 = pd.DataFrame(linhas)
    pd.DataFrame([lin_dia]).to_parquet(SAIDA / f"qid_daily_{dd}.parquet", index=False)
    b5.to_parquet(SAIDA / f"qid_5min_{dd}.parquet", index=False)

    # ---- amostra para inspeção manual: 20 por classe
    rng = np.random.default_rng(SEMENTE)
    imp = ev[ev.tipo == "melhoria"]
    det = ev[ev.tipo == "deterioracao"]
    grupos = {"undercut": imp[imp.classe == "undercut"],
              "recomposicao": imp[imp.classe == "recomposicao"],
              "deslocamento": imp[imp.classe == "deslocamento"],
              "det_execucao": det[det.causa_A == "execucao"],
              "det_cancelamento": det[det.causa_A == "cancelamento"]}
    bruto = pl.read_parquet(caminho(dia)).with_row_index("linha")
    amostras = []
    for nome, g in grupos.items():
        idx = rng.choice(len(g), size=min(N_AMOSTRA, len(g)), replace=False) if len(g) else []
        for _, x in g.iloc[np.sort(idx)].iterrows():
            linha = {"classe_amostra": nome, "hora_brt": hhmmss(x.t_ms), "seq": x.seq, "lado": x.lado,
                     "ticks": x.ticks,
                     "bid_antes": pts(x.b_ant), "ask_antes": pts(x.a_ant),
                     "qbid_antes": x.qb_ant, "qask_antes": x.qa_ant,
                     "bid_depois": pts(x.b), "ask_depois": pts(x.a),
                     "qbid_depois": x.qb, "qask_depois": x.qa,
                     "spread_antes_ticks": x.spread_ant,
                     "teve_negocio": x.teve_negocio, "teve_negocio_nrl": x.teve_negocio_nrl,
                     "agressor": x.agressor, "n_pacotes": x.n_pacotes}
            if x.tipo == "melhoria":
                linha.update({"classe_w100": x.classe_w100, "classe_w1000": x.classe_w1000,
                              "classe_w5000": x.classe_w5000})
                c = contexto_melhoria(x, es, W_PADRAO_MS)
                linha["max_bid_janela_1s"] = pts(c["max_bid_janela_W"]) if c["max_bid_janela_W"] is not None else None
                linha["min_ask_janela_1s"] = pts(c["min_ask_janela_W"]) if c["min_ask_janela_W"] is not None else None
            else:
                linha.update({"causa_A": x.causa_A, "r_10ms": x.r_10ms, "r_1ms": x.r_1ms,
                              "r2_10ms": x.r2_10ms, "r_semrlp_10ms": x.r_semrlp_10ms})
            m = bruto.slice(int(x.linha_ini), int(x.linha_fim) - int(x.linha_ini) + 1)
            tipo = {(0, 0): "N", (0, 1): "C", (0, 2): "D"}
            partes = []
            for row in m.iter_rows(named=True):
                if row["side"] == 2:
                    partes.append(f"T {row['price']:.0f}x{row['size']}{' RLP' if 'RL' in (row['trade_condition'] or '') else ''}")
                else:
                    partes.append(f"{tipo[(0, row['action'])]} {'B' if row['side'] == 0 else 'S'} "
                                  f"{row['price']:.0f}x{row['size']}")
            linha["n_mensagens"] = m.height
            linha["mensagens"] = " | ".join(partes[:25]) + (" | …" if len(partes) > 25 else "")
            amostras.append(linha)
    am = pd.DataFrame(amostras)
    am.to_csv(SAIDA / f"amostra_inspecao_{dd}.csv", index=False)

    resumo = {
        "dia": dia, "janela_valida": [hhmmss(ini), hhmmss(fim)],
        "exclusoes": [[hhmmss(a), hhmmss(b)] for a, b in exc],
        "estados_validos": len(es), "eventos_classificados": len(ev),
        "melhorias": {k: int(v) for k, v in imp.classe.value_counts().items()},
        "melhorias_por_W": {w: {k: int(v) for k, v in imp[f"classe_w{w}"].value_counts().items()}
                            for w in (100, 1000, 5000)},
        "deterioracoes_causa_A": {k: int(v) for k, v in det.causa_A.value_counts().items()},
        "deterioracoes_R10ms": int(det.r_10ms.sum()), "deterioracoes_R10ms_sem_rlp": int(det.r_semrlp_10ms.sum()),
        "deterioracoes_R1ms": int(det.r_1ms.sum()), "deterioracoes_R2_10ms": int(det.r2_10ms.sum()),
        "dia_qids": {k: lin_dia[k] for k in ("qid_R", "qid_R_semrlp", "qid_R_1ms", "qid_R2", "qid_A", "qid_U",
                                              "bqid_R", "aqid_R", "theta", "qid_composicao")},
        "dia_controles": {k: lin_dia[k] for k in ("spread_tw_ticks", "pct_spread_tw", "frac_1tick",
                                                   "volume", "qvol", "delta_spread_ticks")},
        "d1_dia": {k: lin_dia[k] for k in ("delta_spread_ticks", "soma_ticks_det", "soma_ticks_impr", "d1_residuo")},
        "d1_blocos_5min": {"n_blocos": len(b5), "n_residuo_nao_zero": int((b5.d1_residuo != 0).sum())},
        "blocos_qid_ausente": int(b5.qid_R.isna().sum()),
        "share_1tick_impr": lin_dia["share_1tick_impr"], "share_1tick_det_trade": lin_dia["share_1tick_det_trade"],
        "eventos_multipacote": int((ev.n_pacotes > 1).sum()),
        "eventos_classificados_saldo_aberto": int((ev.saldo_final > 0).sum()),
        "eventos_classificados_saldo_negativo": int((ev.saldo_final < 0).sum()),
        "anomalias_livro": cont.livro.n_anom if cont else resumo_ant["anomalias_livro"],
        "conflito_causa_A": cont.n_conflito_causa if cont else resumo_ant["conflito_causa_A"],
        "amostra_por_classe": {k: int(min(N_AMOSTRA, len(g))) for k, g in grupos.items()},
    }
    (SAIDA / f"resumo_{dd}.json").write_text(json.dumps(resumo, indent=2, default=float, ensure_ascii=False))
    return resumo


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], reagregar="--reagregar" in sys.argv,
                          atravessa_ms="--D1b" in sys.argv), indent=2,
                     default=float, ensure_ascii=False))
