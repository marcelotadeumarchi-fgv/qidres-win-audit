"""
Passo 3 da especificação: contagem na amostra inteira + D1 (bloqueante).

Por pregão: eventos classificados, estados, negócios, QID diário e de 5 min.
Junta tudo nos arquivos da especificação (events_classified, qid_daily,
qid_5min) e verifica D1 em TODOS os períodos. Se algum resíduo for != 0,
lista os períodos e sai com código 1.

Uso: python -m auditoria_qidres.passo3_amostra
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import polars as pl

SAIDA = Path(__file__).parent / "saida"
DIAS_DIR = SAIDA / "por_dia"
BASE = Path("/share/fgv-quant/market-data/parquet/2025/09/")


def dias_amostra() -> list[str]:
    tab = pl.read_csv(SAIDA / "tabela_horarios.csv")
    return tab.filter(pl.col("status") == "ok")["dia"].to_list()


def um_dia(dia: str) -> dict:
    os.environ["POLARS_MAX_THREADS"] = "4"
    from .agregacao import agregar_periodo
    from .extracao import extrair, volume_por_instrumento
    from .passo2_um_pregao import blocos_5min, hhmmss
    from .pipeline import janela_valida, rodar_dia

    dd = dia.replace("-", "")
    DIAS_DIR.mkdir(parents=True, exist_ok=True)
    pronto = DIAS_DIR / f"info_{dd}.json"
    if pronto.exists():                     # retomável: pregão já processado
        return json.loads(pronto.read_text())
    extrair(dia)
    r = rodar_dia(dia)                      # D-1' (padrão)
    cont, base = r["contador"], r["base"]
    ev = pd.DataFrame(cont.eventos)
    es = cont.estados_df()
    ng = pd.DataFrame(cont.negocios, columns=["t_ms", "volume"])
    ini, fim, exc = janela_valida(dia)
    assert ev.t_ms.min() >= ini and ev.t_ms.max() < fim, "evento fora da janela válida"
    for a, b in exc:
        assert not ((ev.t_ms >= a) & (ev.t_ms < b)).any(), "evento dentro de exclusão"
    for c in ("b_ant", "a_ant", "b", "a"):
        ev[c + "_pts"] = base + ev[c] * 5.0
    ev["hora_brt"] = ev.t_ms.map(hhmmss)
    ev.to_parquet(DIAS_DIR / f"events_classified_{dd}.parquet", index=False)
    es.to_parquet(DIAS_DIR / f"estados_{dd}.parquet", index=False)
    ng.to_parquet(DIAS_DIR / f"negocios_{dd}.parquet", index=False)

    lin = agregar_periodo(ev, es, ng, ini, fim, {"dia": dia}, exc, base)
    blocos = [agregar_periodo(ev, es, ng, a, b, {"dia": dia, "bloco_ini": hhmmss(a), "bloco_ini_ms": a},
                              exc, base) for a, b in blocos_5min(ini, fim, exc)]
    pd.DataFrame([lin]).to_parquet(DIAS_DIR / f"qid_daily_{dd}.parquet", index=False)
    pd.DataFrame(blocos).to_parquet(DIAS_DIR / f"qid_5min_{dd}.parquet", index=False)
    vol = volume_por_instrumento(dia)
    vol.write_csv(DIAS_DIR / f"volume_instrumentos_{dd}.csv")
    info = {"dia": dia, "eventos": len(ev), "estados": len(es),
            "anomalias_livro": cont.livro.n_anom, "conflito_causa_A": cont.n_conflito_causa,
            "eventos_saldo_aberto": int((ev.saldo_final > 0).sum()),
            "eventos_saldo_negativo": int((ev.saldo_final < 0).sum()),
            "pico_memoria_gb": round(__import__("resource").getrusage(__import__("resource").RUSAGE_SELF).ru_maxrss / 1e6, 1),
            "maior_volume_sid": int(vol["sec_id"][0]), "volume_sid_alvo": int(
                vol.filter(pl.col("sec_id") == 200001274203)["volume"].sum())}
    (DIAS_DIR / f"info_{dd}.json").write_text(json.dumps(info, default=int))
    return info


def main() -> int:
    dias = dias_amostra()
    with ProcessPoolExecutor(max_workers=4, mp_context=mp.get_context("spawn")) as ex:
        infos = list(ex.map(um_dia, dias))
    ler = lambda pref: pd.concat([pd.read_parquet(DIAS_DIR / f"{pref}_{d.replace('-', '')}.parquet")
                                  for d in dias], ignore_index=True)
    ev, qd, q5 = ler("events_classified"), ler("qid_daily"), ler("qid_5min")
    ev.to_parquet(SAIDA / "events_classified.parquet", index=False)
    qd.to_parquet(SAIDA / "qid_daily.parquet", index=False)
    q5.to_parquet(SAIDA / "qid_5min.parquet", index=False)
    pd.DataFrame(infos).to_csv(SAIDA / "passo3_info_por_dia.csv", index=False)

    falhas = pd.concat([qd.assign(periodo="dia")[["dia", "periodo", "d1_residuo", "delta_spread_ticks",
                                                   "soma_ticks_det", "soma_ticks_impr"]],
                        q5.assign(periodo=q5.bloco_ini)[["dia", "periodo", "d1_residuo", "delta_spread_ticks",
                                                         "soma_ticks_det", "soma_ticks_impr"]]])
    ruins = falhas[falhas.d1_residuo != 0]
    resumo = {"dias": len(dias), "periodos_diarios": len(qd), "blocos_5min": len(q5),
              "eventos": len(ev), "d1_periodos_residuo_nao_zero": len(ruins),
              "dias_sid_alvo_nao_e_maior_volume": [i["dia"] for i in infos
                                                   if i["maior_volume_sid"] != 200001274203]}
    (SAIDA / "passo3_resumo.json").write_text(json.dumps(resumo, indent=2, default=int))
    print(json.dumps(resumo, indent=2, default=int))
    if len(ruins):
        ruins.to_csv(SAIDA / "d1_periodos_com_residuo.csv", index=False)
        print("D1 FALHOU: ver saida/d1_periodos_com_residuo.csv", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
