"""
Rodada 3 — extração dos negócios por evento de casamento (Passo 1).

Um registro por evento de casamento com negócio, no pregão contínuo válido:
  t_ms, d (sinal exato pelo lado passivo consumido: +1 compra, −1 venda, 0 se
  indeterminado), so_rlp (só prints RLP, sem ordem visível), vol (todos os
  prints), p_tick (VWAP de todos os prints, em ticks), b_ant/a_ant (melhor
  bid/ask do último estado válido ANTES do evento = t⁻).
Mesmo pipeline do piloto (D-1', D-2), então os ticks usam a mesma base dos
estados salvos em saida_piloto/por_dia.

Uso: python -m auditoria_qidres.rodada3_negocios
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

AQ = Path(__file__).parent
DESTINO = AQ / "saida" / "rodada3"
PILOTO = AQ / "saida_piloto"


def um_dia(dia: str) -> dict:
    os.environ["POLARS_MAX_THREADS"] = "4"
    from .pipeline import rodar_dia
    dd = dia.replace("-", "")
    arq = DESTINO / f"negocios_eventos_{dd}.parquet"
    if arq.exists():
        return {"dia": dia, "eventos": len(pd.read_parquet(arq)), "reaproveitado": True}
    reg = []

    def gancho(e, cont):
        if e.fase != "C" or not e.teve_negocio or cont.ant is None:
            return
        reg.append((e.t, e.seq, e.agressor, (not e.teve_negocio_nrl), e.volume, e.volume_nrl,
                    e.vwap_tick, cont.ant.b, cont.ant.a, e.b, e.a, e.valido))

    r = rodar_dia(dia, ao_fechar=gancho)
    df = pd.DataFrame(reg, columns=["t_ms", "seq", "d", "so_rlp", "vol", "vol_nrl", "p_tick",
                                    "b_ant", "a_ant", "b_pos", "a_pos", "valido_pos"])
    df.insert(0, "dia", dia)
    df["base_pts"] = r["base"]
    # consistência com o piloto: mesma base de ticks dos estados salvos
    ev0 = pd.read_parquet(PILOTO / "por_dia" / f"events_classified_{dd}.parquet", columns=["b", "b_pts"]).iloc[0]
    assert abs((ev0.b_pts - ev0.b * 5.0) - r["base"]) < 1e-9, "base de ticks difere do piloto"
    DESTINO.mkdir(parents=True, exist_ok=True)
    df.to_parquet(arq, index=False)
    return {"dia": dia, "eventos": len(df), "d_exato": int((df.d != 0).sum()), "so_rlp": int(df.so_rlp.sum()),
            "d_zero_nao_rlp": int(((df.d == 0) & ~df.so_rlp).sum())}


def main():
    DESTINO.mkdir(parents=True, exist_ok=True)
    dias = pd.read_csv(PILOTO / "passo3_info_por_dia.csv").dia.tolist()
    with ProcessPoolExecutor(max_workers=4, mp_context=mp.get_context("spawn")) as ex:
        infos = list(ex.map(um_dia, dias))
    pd.DataFrame(infos).to_csv(DESTINO / "extracao_negocios_info.csv", index=False)
    print(json.dumps(infos, indent=1))


if __name__ == "__main__":
    sys.exit(main())
