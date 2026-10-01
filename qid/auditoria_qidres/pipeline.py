"""
Driver: extração -> eventos de casamento -> LivroMBO -> Contador, para um pregão.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl

from .contagem import Contador
from .extracao import caminho, extrair
from .livro import LivroMBO, TICK_PONTOS

TABELA_HORARIOS = Path(__file__).parent / "saida" / "tabela_horarios.csv"
MS_UTC_BRT = -3 * 3_600_000


def ms_brt(sending_time: np.ndarray) -> np.ndarray:
    """YYYYMMDDHHMMSSmmm (UTC) -> ms desde a meia-noite em horário de Brasília."""
    tod = sending_time % 1_000_000_000
    ms = ((tod // 10_000_000) * 3_600_000 + ((tod // 100_000) % 100) * 60_000
          + ((tod // 1_000) % 100) * 1_000 + (tod % 1_000))
    return ms + MS_UTC_BRT


def _ms(dt: str) -> int:
    d = datetime.fromisoformat(dt)
    return ((d.hour * 60 + d.minute) * 60 + d.second) * 1000 + d.microsecond // 1000


def janela_valida(dia: str) -> tuple[int, int, list[tuple[int, int]]]:
    """(ini_ms, fim_ms, exclusões intradiárias) em ms BRT, da tabela de horários."""
    tab = pl.read_csv(TABELA_HORARIOS)
    r = tab.filter(pl.col("dia") == dia).row(0, named=True)
    assert r["status"] == "ok", f"tabela de horários sem status ok para {dia}"
    exc = []
    if r["exclusoes_intradiarias_brt"]:
        for par in r["exclusoes_intradiarias_brt"].split("; "):
            a, b = par.split("-")
            exc.append((_ms(f"{dia} {a}"), _ms(f"{dia} {b}")))
    return _ms(r["janela_valida_ini_brt"]), _ms(r["janela_valida_fim_brt"]), exc


def carregar(dia: str) -> tuple[pl.DataFrame, float, int]:
    df = pl.read_parquet(extrair(dia))
    p = df["price"]
    base = float(p.min()) - 10 * TICK_PONTOS
    tick = ((p - base) / TICK_PONTOS)
    assert ((tick - tick.round(0)).abs() < 1e-9).all(), "preço fora da grade de 5 pontos"
    n = int(round((float(p.max()) - base) / TICK_PONTOS)) + 11
    return df.with_columns(tick=tick.round(0).cast(pl.Int64)), base, n


TETO_CONTINUACAO_MS = 100   # desvio aprovado D-1' (run_log)


def rodar_dia(dia: str, fase_fixa: str | None = None, diagnostico: bool = False,
              atravessa_ms: bool = True, ao_fechar=None):
    """Processa um pregão. `fase_fixa='C'` ignora a tabela (usado só na validação).

    atravessa_ms=False: desvio aprovado D-1 (continuação só com o mesmo sending_time).
    atravessa_ms=True : variante candidata D-1' -- continua enquanto o saldo for
    positivo mesmo se o sending_time mudar, até TETO_CONTINUACAO_MS do início.
    """
    df, base, n = carregar(dia)
    seq = df["msg_seq_num"].to_numpy()
    st = df["sending_time"].to_numpy()
    t = ms_brt(st)
    side = df["side"].to_numpy()
    act = df["action"].to_numpy()
    oid = df["oid"].to_numpy()
    tk = df["tick"].to_numpy()
    q = df["size"].to_numpy()
    rl = df["trade_condition"].fill_null("").str.contains("RL").to_numpy()
    if fase_fixa is None:
        ini, fim, exc = janela_valida(dia)
    livro = LivroMBO(n)
    cont = Contador(livro, dia=dia)
    # fronteiras dos pacotes (msg_seq_num é monotônico em rpt_seq; verificado)
    corte = np.flatnonzero(np.diff(seq) != 0) + 1
    inicios = np.concatenate([[0], corte])
    fins = np.concatenate([corte, [len(seq)]])
    TIPO = {0: "N", 1: "C", 2: "D"}
    diag = [] if diagnostico else None
    aberto = False          # há evento de casamento aberto (saldo != 0)?
    st_ev = None
    t_ev_ini = 0
    linha_ini = 0

    def _fechar(i_fim):
        e = livro.fechar()
        e.linha_ini, e.linha_fim = linha_ini, i_fim
        if ao_fechar is not None:
            ao_fechar(e, cont)              # cont.ant = último estado válido ANTES do evento (t⁻)
        cont.processar_estado(e)
        if diag is not None:
            diag.append((e.seq, e.t, i_fim, e.b, e.a, e.volume, e.volume_nrl, e.q_exec,
                         e.n_exec_msgs, e.teve_negocio, e.agressor, e.n_pacotes, e.saldo_final))

    for i0, i1 in zip(inicios, fins):
        if aberto and st[i0] != st_ev:
            if not atravessa_ms or int(t[i0]) - t_ev_ini > TETO_CONTINUACAO_MS:
                _fechar(i0 - 1)      # D-1: saldo aberto mas o relógio mudou -> fecha
                aberto = False
        if not aberto:
            ti = int(t[i0])
            if fase_fixa is not None:
                fase = fase_fixa
            else:
                fase = "C" if (ini <= ti < fim and not any(a <= ti < b for a, b in exc)) else "X"
            livro.iniciar(int(seq[i0]), ti, fase)
            st_ev = st[i0]
            t_ev_ini = ti
            linha_ini = int(i0)
        msgs = []
        for i in range(i0, i1):
            if side[i] == 2:
                msgs.append(("T", -1, int(tk[i]), int(q[i]), bool(rl[i])))
            else:
                msgs.append((TIPO[int(act[i])], int(side[i]), int(tk[i]), int(q[i]), int(oid[i])))
        saldo = livro.pacote(msgs)
        if saldo <= 0:             # < 0: execução atribuída além do volume (contado em saldo_final)
            _fechar(int(i1) - 1)
            aberto = False
        else:
            aberto = True
    if aberto:
        _fechar(len(seq) - 1)
    out = {"contador": cont, "base": base, "n_ticks": n}
    if diag is not None:
        out["diag"] = pl.DataFrame(diag, schema=["seq", "t_ms", "ultima_linha", "b", "a", "volume", "volume_nrl",
                                                  "q_exec", "n_exec_msgs", "teve_negocio", "agressor",
                                                  "n_pacotes", "saldo_final"],
                                   orient="row")
    return out
