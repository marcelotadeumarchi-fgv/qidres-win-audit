# QIDres audit on the WIN mini-index future (B3)

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23091936.svg)](https://doi.org/10.5281/zenodo.23091936)


Audit of the informed-trading measure **QIDres** (Barardehi, Dixon & Liu, *Journal of Finance*,
“Detecting Informed Trading Risk from Undercutting Activity”) on market-by-order (MBO) data for the
B3 mini-index future **WINV25** (`security_id 200001274203`), 12 trading sessions, 15–30 Sep 2025.

The question is methodological: in a tick-constrained market, does QIDres capture undercutting, or
only the composition of quote deteriorations (execution vs. cancellation)? Later rounds test whether
block-level adverse selection exists at all in this sample.

Documentation is in Portuguese:

| File | What it is |
|---|---|
| `qid/auditoria_qidres/auditoria_qidres_win.md` | Specification (source of truth) |
| `qid/auditoria_qidres/relatorio.md` | Report: pilot (sections 1–4), round 3 (section 7), round 3b (section 8), E4 (section 9) |
| `qid/auditoria_qidres/run_log.md` | Decisions, approved deviations (D-1′, D-2, D-3, D-4), pre-registration, exploratory tests (E1–E4), code hashes |
| `MANIFEST.md` | Provenance: every file’s original path and SHA-256 |

## How to cite

Marchi, M. T. (2026). *QIDres audit on the WIN mini-index future (B3): code, specification and results*
(Version 1.0.0) [Software]. Zenodo. https://doi.org/10.5281/zenodo.23091937

- All versions (concept DOI, always resolves to the latest): https://doi.org/10.5281/zenodo.23091936
- Version 1.0.0: https://doi.org/10.5281/zenodo.23091937

The audited measure is from Barardehi, Y. H., Dixon, P., & Liu, Q., “Detecting Informed Trading Risk from
Undercutting Activity”, *Journal of Finance* (forthcoming), https://papers.ssrn.com/abstract=4689334.

## Layout

```
qid/
  auditoria_qidres/          the audit (Python package)
    livro.py contagem.py agregacao.py pipeline.py extracao.py horarios.py regressao.py
    passo2_*.py … passo6_*.py           spec steps 2–6 (pilot)
    tarefa1–3_*.py                      round 2 (Task 1 was interrupted; 2–3 never run on data)
    rodada3*.py                         round 3 (viability), 3b (E2–E3), 3c (E4)
    testes/test_sinteticos.py           20 synthetic tests
    scripts/rodar_*.sh                  detached runners used during the work
    saida_piloto/                       pilot results (sections 1–6 of the report)
    saida/                              round 3, 3b and 3c results (sections 7–9)
  undercutting_valida.py     reference order-book replay (also builds the 1-min panel)
  fila_pipeline.py           1-second queue panel (FQCR, runs) — input to passo6, tarefa3, rodada3b
  protocolo_estimar.py       qid_signed (H1 of the earlier protocol) — input to passo6
  tese_apendice_a.py         verbatim reproduction of the thesis Appendix A (qid_res) — input to passo6
  spec_hipoteses.py          H2/P2a queue measures (definitions reused in passo6)
  output_data/               small upstream outputs needed by passo6 (large ones are git-ignored)
history/                     earlier research: session record, pre-registered protocols, scripts,
                             text results, figures (< 5 MB). Kept for context, not maintained.
```

## Data

Raw data is **not** in this repository. The code reads B3 UMDF MBO Parquet files from
`/share/fgv-quant/market-data/parquet/2025/09/<day>/10/incremental/MBO/` (constant `BASE` in
`qid/auditoria_qidres/extracao.py` and `horarios.py`; `BASE_INPUT_DIR` in `qid/undercutting_valida.py`).
Change those constants if the data lives elsewhere.

## Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd qid
python -m auditoria_qidres.testes.test_sinteticos      # expect: 20/20 testes passaram
```

All modules are run from `qid/` with `python -m auditoria_qidres.<module>`.

## Regenerating the data (≈ 10 GB, git-ignored)

Run from `qid/`, in this order. Memory: about 15 GB per session process; `passo3_amostra` and
`rodada3_negocios` run 4 sessions in parallel.

1. `python -m auditoria_qidres.horarios` — trading-hours table from the feed (`saida/tabela_horarios.csv`).
2. `python -m auditoria_qidres.passo3_amostra` — extraction cache (`auditoria_qidres/cache/`), per-session
   counts, D1 check. The report’s pilot used the output folder `saida_piloto/`; round 3 code reads from there.
3. `python -m auditoria_qidres.passo4_diagnosticos`, `passo5_qidres`, `passo6_comparacao` — pilot steps 4–6.
   `passo6` also needs `output_data/spec_fila/painel_200001274203.parquet`: build it with
   `python fila_pipeline.py 200001274203` (uses `undercutting_valida`).
4. Round 3: `rodada3_negocios` → `rodada3_viabilidade`; then `rodada3b_robustez`; then `rodada3c_theta`.

`valida_replay.py <day>` cross-checks the audit’s book engine against `undercutting_valida.replay_dia`
(0 best-bid/ask divergences on 2025-09-30).

## Notes

- `saida_piloto/` vs `saida/`: the pilot outputs were archived to `saida_piloto/` before round 2; round 2
  Task 1 was stopped by the user, so `saida/` holds only round-3 results.
- Results are reported without interpretive verdicts by design; the interpretation is the author’s.
- `history/scripts/qid_strict.py` and `diagnostico_overlap.py` still contain absolute paths and are not
  expected to run as-is.
