"""
Tabela de horários de pregão e leilões do WIN, por data, a partir das
mensagens SecurityStatus (msg_type 'f') do próprio feed. Nada é fixo no código.

Status (SecurityTradingStatus, UMDF B3) observados no nível do instrumento:
   21  pré-abertura (leilão)        17  aberto (negociação contínua)
  101  call de fechamento          18  fechado / indisponível
    2  pausa (leilão/halt)          4  sem abertura
Horários: o feed está em UTC; convertidos para Brasília (UTC-3, sem horário de
verão desde 2019).

Janela válida (especificação, "Horário"): pregão contínuo, excluindo
  * 15 min após a abertura (fim do leilão de abertura),
  * 15 min antes do início do call de fechamento,
  * leilões intradiários e 1 min após cada um.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

BASE = Path("/share/fgv-quant/market-data/parquet/2025/09/")
SID = 200001274203
SAIDA = Path(__file__).parent / "saida"
UTC_BRT = timedelta(hours=-3)
EXC_ABERTURA = timedelta(minutes=15)
EXC_FECHAMENTO = timedelta(minutes=15)
EXC_POS_LEILAO = timedelta(minutes=1)


def _dt(st: int) -> datetime:
    return datetime.strptime(str(st), "%Y%m%d%H%M%S%f") if len(str(st)) == 17 else None


def _ts(st: int) -> datetime:
    s = str(st)
    return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]), int(s[10:12]),
                    int(s[12:14]), int(s[14:17]) * 1000)


def mensagens_status(dia_dir: Path) -> pl.DataFrame:
    arqs = [str(p) for p in sorted(dia_dir.rglob("*.parquet"))]
    lf = pl.scan_parquet(arqs).filter(pl.col("msg_type").is_in(["f", "B"]))
    cols = ["msg_type", "msg_seq_num", "sending_time", "security_id", "security_group",
            "trading_session_sub_id", "security_trading_status", "security_trading_event",
            "trad_ses_open_time", "headline"]
    return lf.select(cols).collect().sort("msg_seq_num")


def tabela_dia(dia_dir: Path, dia: str) -> tuple[dict, pl.DataFrame]:
    m = mensagens_status(dia_dir)
    inst = m.filter((pl.col("msg_type") == "f") & (pl.col("security_id") == SID))
    trans = [(_ts(r["sending_time"]) + UTC_BRT, int(r["security_trading_status"]),
              r["security_trading_event"], r["trad_ses_open_time"])
             for r in inst.iter_rows(named=True)]
    linha = {"dia": dia, "n_msgs_status_instrumento": len(trans)}
    if not trans:
        linha["status"] = "sem mensagens de status do instrumento"
        return linha, inst
    # abertura: primeira transição para 17 após pré-abertura (21)
    abertura = next((t for t, s, *_ in trans if s == 17), None)
    call_ini = next((t for t, s, *_ in trans if s == 101), None)
    fech = next((t for t, s, *_ in trans if s == 18 and call_ini and t >= call_ini), None)
    # leilões intradiários: saída de 17 para 21/2 entre abertura e call, e volta a 17
    leiloes, ini_l = [], None
    for t, s, *_ in trans:
        if abertura and call_ini and abertura < t < call_ini:
            if s in (21, 2) and ini_l is None:
                ini_l = t
            elif s == 17 and ini_l is not None:
                leiloes.append((ini_l, t))
                ini_l = None
    linha.update({
        "abertura_brt": abertura, "inicio_call_brt": call_ini, "fechamento_brt": fech,
        "n_leiloes_intradiarios": len(leiloes),
        "leiloes_intradiarios_brt": "; ".join(f"{a:%H:%M:%S.%f}"[:-3] + "-" + f"{b:%H:%M:%S.%f}"[:-3]
                                              for a, b in leiloes),
        "janela_valida_ini_brt": abertura + EXC_ABERTURA if abertura else None,
        "janela_valida_fim_brt": call_ini - EXC_FECHAMENTO if call_ini else None,
        "status": "ok" if (abertura and call_ini and fech) else "incompleto",
    })
    linha["exclusoes_intradiarias_brt"] = "; ".join(
        f"{a:%H:%M:%S.%f}"[:-3] + "-" + f"{b + EXC_POS_LEILAO:%H:%M:%S.%f}"[:-3] for a, b in leiloes)
    if linha["janela_valida_ini_brt"] and linha["janela_valida_fim_brt"]:
        dur = linha["janela_valida_fim_brt"] - linha["janela_valida_ini_brt"]
        dur -= sum(((b + EXC_POS_LEILAO) - a for a, b in leiloes), timedelta())
        linha["minutos_validos"] = round(dur.total_seconds() / 60, 2)
    return linha, inst


def main():
    SAIDA.mkdir(exist_ok=True)
    dias = sorted(p for p in BASE.iterdir() if p.is_dir())
    linhas, brutos = [], []
    for d in dias:
        dia_dir = d / "10" / "incremental"
        if not dia_dir.exists():
            continue
        linha, inst = tabela_dia(dia_dir, f"2025-09-{d.name}")
        linhas.append(linha)
        if inst.height:
            brutos.append(inst.with_columns(dia=pl.lit(linha["dia"])))
        print(linha["dia"], linha.get("status"), file=sys.stderr)
    tab = pl.DataFrame(linhas, infer_schema_length=None)
    tab.write_csv(SAIDA / "tabela_horarios.csv")
    if brutos:
        pl.concat(brutos, how="diagonal").drop("headline").write_csv(SAIDA / "status_instrumento_bruto.csv")
    return tab


if __name__ == "__main__":
    pl.Config.set_tbl_rows(50); pl.Config.set_tbl_cols(20); pl.Config.set_tbl_width_chars(250)
    print(main())
