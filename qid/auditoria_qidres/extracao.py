"""
Extração do fluxo de eventos do WIN com o agrupamento por evento de casamento.

Mesmo filtro de `undercutting_valida.extrair_eventos_dia` (tipos 0/1/2, ações
0/1/2, preço não nulo, ordenado por rpt_seq), acrescido de `msg_seq_num` e
`last_fragment`, que o cache existente não guarda. Cache próprio em
auditoria_qidres/cache/ -- o cache do replay existente não é tocado.
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

BASE = Path("/share/fgv-quant/market-data/parquet/2025/09/")
SID = 200001274203
CACHE = Path(__file__).parent / "cache"


def caminho(dia: str, sid: int = SID) -> Path:
    return CACHE / f"eventos_{sid}_{dia.replace('-', '')}.parquet"


def extrair(dia: str, sid: int = SID) -> Path:
    CACHE.mkdir(exist_ok=True)
    out = caminho(dia, sid)
    if out.exists():
        return out
    dd = BASE / dia[-2:] / "10" / "incremental"
    arqs = [str(p) for p in sorted(dd.rglob("*.parquet"))]
    e = pl.col("md_entries")
    px = e.struct.field("md_entry_px")
    lf = (
        pl.scan_parquet(arqs)
        .filter(pl.col("msg_type") == "X")
        .select("msg_seq_num", "sending_time", "last_fragment", "rpt_seq", "security_id", "md_entries")
        .explode("md_entries")
        .with_columns(
            sec_id=pl.coalesce([e.struct.field("security_id"), pl.col("security_id")]),
            entry_type=e.struct.field("md_entry_type"),
            action=e.struct.field("md_update_action"),
            order_id=e.struct.field("order_id"),
            mantissa=px.struct.field("mantissa"),
            exponent=px.struct.field("exponent"),
            size=e.struct.field("md_entry_size"),
            rpt=pl.coalesce([e.struct.field("rpt_seq"), pl.col("rpt_seq")]),
            trade_condition=e.struct.field("trade_condition"),
        )
        .filter((pl.col("sec_id") == sid) & pl.col("entry_type").is_in(["0", "1", "2"])
                & pl.col("action").is_in([0, 1, 2]) & pl.col("mantissa").is_not_null())
        .select(
            pl.col("rpt").cast(pl.Int64),
            pl.col("msg_seq_num").cast(pl.Int64),
            pl.col("last_fragment").cast(pl.Int64, strict=False),
            pl.col("sending_time").cast(pl.Int64),
            pl.col("entry_type").replace_strict({"0": 0, "1": 1, "2": 2}, return_dtype=pl.Int8).alias("side"),
            pl.col("action").cast(pl.Int8),
            pl.col("order_id").cast(pl.Int64, strict=False).fill_null(-1).alias("oid"),
            (pl.col("mantissa") * (pl.lit(10.0) ** pl.col("exponent"))).alias("price"),
            pl.col("size").cast(pl.Int64).fill_null(0),
            pl.col("trade_condition"),
        )
        .sort("rpt")
    )
    lf.sink_parquet(out, compression="zstd")
    return out


def volume_por_instrumento(dia: str) -> pl.DataFrame:
    """Volume negociado por security_id (regra 'vencimento de maior volume').

    Extrai só os 4 campos necessários ANTES de expandir a lista de entradas e
    agrega em streaming: expandir o struct inteiro (~70 campos, todos os
    instrumentos) chegou a 148 GB num processo.
    """
    dd = BASE / dia[-2:] / "10" / "incremental"
    arqs = [str(p) for p in sorted(dd.rglob("*.parquet"))]
    el = pl.element()
    return (pl.scan_parquet(arqs).filter(pl.col("msg_type") == "X")
            .select(pl.col("security_id").alias("sid_msg"),
                    pl.col("md_entries").list.eval(pl.struct(
                        sid=el.struct.field("security_id"), t=el.struct.field("md_entry_type"),
                        a=el.struct.field("md_update_action"), q=el.struct.field("md_entry_size"))).alias("e"))
            .explode("e").unnest("e")
            .filter((pl.col("t") == "2") & (pl.col("a") == 0))
            .with_columns(sec_id=pl.coalesce([pl.col("sid"), pl.col("sid_msg")]))
            .group_by("sec_id").agg(volume=pl.col("q").sum(), negocios=pl.len())
            .collect(engine="streaming")
            .sort("volume", descending=True).with_columns(dia=pl.lit(dia)))


if __name__ == "__main__":
    for d in sys.argv[1:]:
        print(extrair(d))
