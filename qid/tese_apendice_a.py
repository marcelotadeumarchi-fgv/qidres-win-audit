"""
VERBATIM REPRODUCTION OF THE THESIS APPENDIX A PIPELINE.

Transcribed from Marchi_Qualificacao, Appendix A, changing ONLY:
  * output directory (to avoid clobbering other results)
  * pl.count() -> pl.len()   (renamed in current polars)
Everything substantive -- the forward-fill without an action filter, the
single-sided undercut test, the per-5-min window state reset, the large-tick
filter and the daily OLS -- is left exactly as written in the thesis.

Purpose: determine whether the thesis's own code, run on this data, reproduces
the reported R2 of 10.93%. If it does, the difference from the validated
measure is the MEASURE. If it does not, the difference lies elsewhere.
"""

import os
from pathlib import Path
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
import polars as pl

warnings.filterwarnings("ignore")

TARGET_SECURITY_ID = 200001274203
TICK_SIZE = 5.0
BASE_INPUT_DIR = Path("/share/fgv-quant/market-data/parquet/2025/09/")
OUTPUT_DIR = Path(__file__).resolve().parent / "output_data" / "tese_apendice_a"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def processar_dia_completo(day_dir: Path) -> str:
    date_str = day_dir.name
    output_file_day = OUTPUT_DIR / f"qid_res_5m_{TARGET_SECURITY_ID}_{date_str}.parquet"
    if output_file_day.exists():
        return f"[-] Dia {date_str} ja processado."

    parquet_files = [str(p) for p in sorted(day_dir.rglob("*.parquet"))]
    if not parquet_files:
        return f"[!] Dia {date_str}: Vazio."

    try:
        lazy_plans = [
            pl.scan_parquet(f_path).explode("md_entries").with_columns(
                entry_sec_id=pl.col("md_entries").struct.field("security_id"),
                entry_type=pl.col("md_entries").struct.field("md_entry_type").cast(pl.String),
                md_update_action=pl.col("md_entries").struct.field("md_update_action"),
                mantissa=pl.col("md_entries").struct.field("md_entry_px").struct.field("mantissa").cast(pl.Float64),
                exponent=pl.col("md_entries").struct.field("md_entry_px").struct.field("exponent").cast(pl.Float64),
                time_seq=pl.coalesce([pl.col("transact_time"), pl.col("sending_time"), pl.col("msg_seq_num")]),
            ).with_columns(
                sec_id_final=pl.coalesce(["security_id", "entry_sec_id"])
            ).filter(
                (pl.col("sec_id_final") == TARGET_SECURITY_ID)
                | (pl.col("sec_id_final").cast(pl.String) == str(TARGET_SECURITY_ID))
            ).with_columns(
                price=pl.col("mantissa") * (10.0 ** pl.col("exponent"))
            ).filter(
                pl.col("price").is_not_null() & (pl.col("price") > 0)
            ).select(["time_seq", "entry_type", "price", "md_update_action"])
            for f_path in parquet_files
        ]

        eager_dfs = pl.collect_all(lazy_plans)
        valid_dfs = [df for df in eager_dfs if len(df) > 0]
        if not valid_dfs:
            return f"[!] Dia {date_str}: Ativo nao encontrado."

        df_day = pl.concat(valid_dfs).sort("time_seq")

        df_day = df_day.with_columns(
            datetime=pl.col("time_seq").cast(pl.String).str.to_datetime("%Y%m%d%H%M%S%3f", strict=False)
        ).with_columns(window_5m=pl.col("datetime").dt.truncate("5m"))

        df_topo = df_day.with_columns(
            bid_raw=pl.when(pl.col("entry_type") == "0").then(pl.col("price")),
            ask_raw=pl.when(pl.col("entry_type") == "1").then(pl.col("price")),
        ).with_columns(
            current_bid=pl.col("bid_raw").forward_fill().over("window_5m"),
            current_ask=pl.col("ask_raw").forward_fill().over("window_5m"),
        ).with_columns(
            prev_best_bid=pl.col("current_bid").shift(1).over("window_5m"),
            prev_best_ask=pl.col("current_ask").shift(1).over("window_5m"),
            midpoint=(pl.col("current_ask") + pl.col("current_bid")) / 2.0,
        )

        df_final = df_topo.with_columns(
            is_undercutting=(
                ((pl.col("entry_type") == "0") & (pl.col("md_update_action") == 0)
                 & (pl.col("prev_best_bid").is_not_null()) & (pl.col("price") > pl.col("prev_best_bid")))
                | ((pl.col("entry_type") == "1") & (pl.col("md_update_action") == 0)
                   & (pl.col("prev_best_ask").is_not_null()) & (pl.col("price") < pl.col("prev_best_ask")))
            )
        ).with_columns(
            spread_pts=pl.when(pl.col("current_ask") > pl.col("current_bid"))
            .then(pl.col("current_ask") - pl.col("current_bid")).otherwise(None)
        )

        qid_timeseries = df_final.group_by("window_5m").agg([
            pl.col("is_undercutting").sum().alias("undercutting_count"),
            pl.len().alias("total_messages"),
            pl.col("spread_pts").mean().alias("avg_spread_pts"),
            pl.col("midpoint").mean().alias("avg_midpoint"),
            pl.col("midpoint").std().alias("midpoint_volatility"),
        ]).with_columns(
            spread_relativo=pl.col("avg_spread_pts") / pl.col("avg_midpoint"),
            qid_ratio=pl.col("undercutting_count") / pl.col("total_messages"),
        ).sort("window_5m")

        df_regressao = qid_timeseries.filter(
            (pl.col("avg_spread_pts") > TICK_SIZE)
            & (pl.col("spread_relativo").is_not_null())
            & (pl.col("qid_ratio").is_not_null())
        )
        if len(df_regressao) < 10:
            return f"[!] Dia {date_str}: Janelas insuficientes."

        x = df_regressao["spread_relativo"]
        y = df_regressao["qid_ratio"]
        beta = pl.cov(x, y) / x.var()
        alpha = y.mean() - beta * x.mean()

        df_final_day = df_regressao.with_columns(
            qid_res=pl.col("qid_ratio") - (alpha + beta * pl.col("spread_relativo"))
        )
        df_final_day.write_parquet(output_file_day)
        return f"[OK] Dia {date_str}: {len(df_regressao)} janelas."
    except Exception as e:
        return f"[ERRO] Dia {date_str}: {type(e).__name__}: {e}"


if __name__ == "__main__":
    day_directories = sorted([p for p in BASE_INPUT_DIR.iterdir() if p.is_dir()])
    with ProcessPoolExecutor(max_workers=6) as executor:
        for future in as_completed({executor.submit(processar_dia_completo, d): d
                                    for d in day_directories}):
            print(future.result(), flush=True)

    files = sorted(list(OUTPUT_DIR.glob(f"qid_res_5m_{TARGET_SECURITY_ID}_*.parquet")))
    if files:
        pl.concat([pl.read_parquet(f) for f in files]).sort("window_5m").write_parquet(
            OUTPUT_DIR / f"qid_res_5m_FULL_MONTH_{TARGET_SECURITY_ID}.parquet")
        print("Processamento mensal concluido!")
