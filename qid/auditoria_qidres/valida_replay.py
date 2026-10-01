"""
Passo 2, pré-condição: o motor por evento de casamento (LivroMBO) reproduz a
reconstrução existente (`undercutting_valida.replay_dia`)?

Compara, ao fim de cada evento de casamento, o melhor bid/ask do LivroMBO com o
melhor bid/ask que o replay existente registra após a última mensagem de book
do mesmo evento. Exige zero divergências.

Também testa se um evento de casamento pode atravessar mais de um msg_seq_num
(o feed não preenche `last_fragment`): em todo pacote com negócio, a quantidade
negociada deve ser igual à retirada do lado passivo por execução no mesmo pacote.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import undercutting_valida as uv  # noqa: E402

from .pipeline import rodar_dia  # noqa: E402

SAIDA = Path(__file__).parent / "saida"


def validar(dia: str) -> dict:
    dd = dia.replace("-", "")
    r_novo = rodar_dia(dia, fase_fixa="C", diagnostico=True)
    diag = r_novo["diag"]
    df_ext = pl.read_parquet(Path(__file__).parent / "cache" / f"eventos_200001274203_{dd}.parquet",
                             columns=["side"])
    nao_neg = (df_ext["side"].to_numpy() != 2)
    # posição, entre as linhas de book, de cada linha do arquivo
    pos_book = np.cumsum(nao_neg) - 1

    antigo = uv._events_path(dd)
    rep = uv.replay_dia(antigo, bucket="1m", rec_ini_ms=0, rec_fim_ms=86_400_000)
    rec = rep["events"]
    rec_bb = np.fromiter((x[4] for x in rec if x[1] != 9), dtype=np.int64)
    rec_ba = np.fromiter((x[5] for x in rec if x[1] != 9), dtype=np.int64)
    assert len(rec_bb) == int(nao_neg.sum()), (len(rec_bb), int(nao_neg.sum()))
    assert abs(rep["tick_base"] - r_novo["base"]) < 1e-9, (rep["tick_base"], r_novo["base"])

    # último índice de linha de BOOK de cada evento (eventos só com negócio não têm)
    ult = diag["ultima_linha"].to_numpy()
    # para cada evento, a última linha de book em [i0, i1]: como as linhas de book
    # são contadas cumulativamente, é pos_book[ultima_linha] se o evento contém
    # ao menos uma linha de book depois do evento anterior
    pb = pos_book[ult]
    pb_ant = np.concatenate([[-1], pb[:-1]])
    tem_book = pb > pb_ant
    n_nov = r_novo["n_ticks"]
    b_novo = diag["b"].to_numpy()[tem_book]
    a_novo = diag["a"].to_numpy()[tem_book]
    b_rep = rec_bb[pb[tem_book]]
    a_rep = rec_ba[pb[tem_book]]
    # convenção de lado vazio: replay usa -1 / n_ticks; LivroMBO também
    div_b = int((b_novo != b_rep).sum())
    div_a = int((a_novo != a_rep).sum())

    neg = diag.filter(pl.col("volume_nrl") > 0)
    frag = neg.filter(pl.col("saldo_final") != 0)
    res = {
        "dia": dia,
        "eventos_de_casamento": diag.height,
        "eventos_comparados": int(tem_book.sum()),
        "divergencias_melhor_bid": div_b,
        "divergencias_melhor_ask": div_a,
        "eventos_com_negocio_nao_rlp": neg.height,
        "eventos_multipacote": int((diag["n_pacotes"] > 1).sum()),
        "eventos_saldo_fecha_zero": int((neg["saldo_final"] == 0).sum()),
        "eventos_saldo_aberto_positivo": int((neg["saldo_final"] > 0).sum()),
        "eventos_saldo_negativo": int((neg["saldo_final"] < 0).sum()),
        "volume_nao_rlp": int(neg["volume_nrl"].sum()),
        "volume_saldo_aberto": int(neg.filter(pl.col("saldo_final") > 0)["saldo_final"].sum()),
        "anomalias_livro": r_novo["contador"].livro.n_anom,
        "conflito_causa_A": r_novo["contador"].n_conflito_causa,
        "replay_integridade": {k: rep["report"][k] for k in
                               ("orphan_deletes", "orphan_changes", "neg_size_ticks")},
    }
    SAIDA.mkdir(exist_ok=True)
    (SAIDA / f"validacao_replay_{dd}.json").write_text(json.dumps(res, indent=2, default=int))
    if frag.height:
        frag.head(200).write_csv(SAIDA / f"pacotes_volume_diferente_execucao_{dd}.csv")
    return res


if __name__ == "__main__":
    print(json.dumps(validar(sys.argv[1]), indent=2, default=int))
