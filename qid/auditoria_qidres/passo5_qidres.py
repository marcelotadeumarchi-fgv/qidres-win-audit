"""
Passo 5: QIDres (desvio aprovado D-3: unidade hora, janela semana anterior,
efeitos fixos de horário) em todas as versões e variantes + D8–D11.

Horas: [início da grade de 5 min (I8), 10:00), [10:00, 11:00), …, [18:00, fim da
janela válida). Exclusões intradiárias removidas (interpretação I9).
Variante Rolling: parâmetros das 45 horas anteriores (~5 pregões; análogo aos
60 pregões da especificação na escala horária; interpretação I10).
"Spread em reais" (MC): spread em pontos; ln difere só por constante (1 ponto =
R$ 0,20 por contrato), o que não altera a inclinação.

Uso: python -m auditoria_qidres.passo5_qidres
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from .agregacao import agregar_periodo
from .passo2_um_pregao import BLOCO_MS, hhmmss
from .passo4_diagnosticos import B, SEMENTE, boot_por_dia
from .pipeline import janela_valida
from .regressao import _desenho, _ols, qidres_por_janela, winsorizar

SAIDA = Path(__file__).parent / "saida"
DIAS_DIR = SAIDA / "por_dia"
HORA_MS = 3_600_000
JANELA_MOVEL_H = 45

VERSOES = ["qid_R", "qid_A", "qid_U", "bqid_R", "aqid_R"]
EXTRAS = ["qid_R_semrlp", "qid_R2"]
LIMIARES = {"D8": "R² < 0,05 OU sinal de b diferente entre semanas",
            "D9": "DP > 2,0 OU |AR(1)| > 0,3",
            "D10": "|ρ| > 0,3",
            "D11": "ρ(QIDres^R, QIDres^U) < 0,5"}


def horas_dia(dia: str):
    ini, fim, exc = janela_valida(dia)
    a = -(-ini // BLOCO_MS) * BLOCO_MS
    out = []
    while a < fim:
        b = min((a // HORA_MS + 1) * HORA_MS, fim)
        out.append((a, b))
        a = b
    return out, exc


def painel_horario(dias: list[str]) -> pd.DataFrame:
    linhas = []
    for dia in dias:
        dd = dia.replace("-", "")
        ev = pd.read_parquet(DIAS_DIR / f"events_classified_{dd}.parquet")
        es = pd.read_parquet(DIAS_DIR / f"estados_{dd}.parquet")
        ng = pd.read_parquet(DIAS_DIR / f"negocios_{dd}.parquet")
        base = float(ev.b_pts.iloc[0] - ev.b.iloc[0] * 5.0)
        hs, exc = horas_dia(dia)
        for a, b in hs:
            lin = agregar_periodo(ev, es, ng, a, b, {"dia": dia, "hora_ini": hhmmss(a), "h": a // HORA_MS}, exc, base)
            lin["minutos"] = (b - a - sum(max(0, min(b, y) - max(a, x)) for x, y in exc)) / 60_000
            lin["hora_ts"] = pd.Timestamp(dia) + pd.Timedelta(milliseconds=a)
            linhas.append(lin)
    p = pd.DataFrame(linhas)
    p["semana"] = pd.to_datetime(p.dia).dt.to_period("W").dt.start_time.dt.date.astype(str)
    p["ln_pct_spread"] = np.log(p.pct_spread_tw)
    p["ln_spread_pts"] = np.log(p.spread_tw_ticks * 5.0)
    return p


def rolling_fe(p: pd.DataFrame, y: str, xs: list[str], fe: str, n: int, rotulo: str) -> pd.DataFrame:
    p = p.sort_values("hora_ts").reset_index(drop=True)
    out = []
    for i in range(len(p)):
        r = {"hora_ts": p.loc[i, "hora_ts"], "qidres": np.nan, "status": "nao_identificada"}
        if i >= n:
            est = p.iloc[i - n:i].dropna(subset=[y] + xs)
            assert est.hora_ts.max() < p.loc[i, "hora_ts"], "janela móvel inclui o futuro"
            X, nomes, niveis = _desenho(est, xs, fe, None)
            fit = _ols(est[y].to_numpy(float), X)
            s = float(est[y].std(ddof=1))
            if fit["status"] == "ok" and s > 0 and p.loc[i, fe] in niveis:
                x1, _, _ = _desenho(p.iloc[[i]], xs, fe, niveis)
                fv = fit["beta"][0] + float(x1[0] @ fit["beta"][1:])
                r.update(qidres=-(p.loc[i, y] - fv) / s, status="ok")
        out.append(r)
    o = pd.DataFrame(out)
    o["versao"] = rotulo
    return o


def marcar_u_fraco(d: pd.DataFrame) -> pd.DataFrame:
    """Aprovado pelo usuário: células com QID^U são fracamente identificadas
    (maioria dos períodos com 0 undercut -> QID^U = −1; S(QID^U) ~ 0,005)."""
    m = d.id.isin(["D9", "D10", "D11"]) & d.diagnostico.str.contains("qid_U")
    d["cruzou_limiar"] = d["cruzou_limiar"].astype(object)
    d.loc[m, "cruzou_limiar"] = "fracamente identificada"
    d.loc[m, "obs"] = (d.loc[m, "obs"].fillna("").astype(str)
                       + "; QID^U degenerado: maioria dos períodos sem undercut")
    return d


def main():
    dias = pd.read_csv(SAIDA / "passo3_info_por_dia.csv").dia.tolist()
    p = painel_horario(dias)
    assert (p.d1_residuo == 0).all(), "D1 falhou na agregação horária"
    p.to_parquet(SAIDA / "qid_hourly.parquet", index=False)

    especs = []        # (rotulo, y, xs)
    for y in VERSOES + EXTRAS:
        especs.append((f"{y}|base", y, ["ln_pct_spread"]))
    for y in VERSOES:
        especs.append((f"{y}|MC", y, ["ln_spread_pts", "qvol", "volume"]))
        especs.append((f"{y}|Frac1", y, ["frac_1tick"]))
    especs.append(("qid_R_1ms|1ms", "qid_R_1ms", ["ln_pct_spread"]))

    res_all, fs_all = [], []
    for rot, y, xs in especs:
        r, fs = qidres_por_janela(p, y, xs, "semana", "hora_ts", fe="h", rotulo=rot)
        res_all.append(r[["hora_ts", "semana", "janela_param", "qidres", "status", "versao"]])
        fs_all.append(fs)
    for y in VERSOES:
        res_all.append(rolling_fe(p, y, ["ln_pct_spread"], "h", JANELA_MOVEL_H, f"{y}|Rolling"))
    res = pd.concat(res_all, ignore_index=True)
    # Winsor: QIDres base winsorizada em 1%/99% sobre a amostra (um instrumento)
    for y in VERSOES:
        w = res[res.versao == f"{y}|base"].copy()
        w["qidres"] = winsorizar(w.qidres)
        w["versao"] = f"{y}|Winsor"
        res = pd.concat([res, w], ignore_index=True)
    res.to_parquet(SAIDA / "qidres_hourly.parquet", index=False)
    fs = pd.concat(fs_all, ignore_index=True)
    fs.to_csv(SAIDA / "first_stage.csv", index=False)

    # ---- D8–D11
    rng = np.random.default_rng(SEMENTE)
    L = []
    def lin(id_, nome, nivel, valor, ic, n, ref, alerta, obs=""):
        ident = isinstance(valor, (int, float)) and np.isfinite(valor)
        L.append({"id": id_, "diagnostico": nome, "nivel": nivel, "valor": valor if ident else "não identificada",
                  "ic95_inf": ic[0], "ic95_sup": ic[1], "N": n, "referencia_artigo": ref,
                  "limiar": LIMIARES[id_], "cruzou_limiar": bool(alerta) if ident else "não identificada", "obs": obs})

    base_fs = fs[fs.versao.str.endswith("|base") | fs.versao.str.endswith("|1ms")]
    for _, f in base_fs.iterrows():
        bcol = [c for c in f.index if c.startswith("b_") and not c.startswith("b_fe")][0]
        v = base_fs[base_fs.versao == f.versao][bcol].dropna()
        troca = len(v) > 1 and (np.sign(v).nunique() > 1)
        r2 = f.get("R2", np.nan)
        lin("D8", f"R² e b do passo 1 [{f.versao}] semana {f.janela}", "hora",
            float(r2) if pd.notna(r2) else np.nan, (np.nan, np.nan), int(f.N), "R² 47,35% (Fig. E.1, entre ações)",
            (pd.notna(r2) and r2 < 0.05) or troca,
            f"b={f.get(bcol, np.nan):.4g} (EP {f.get('se_' + bcol, np.nan):.3g}); S(QID)={f.S_QID:.4g}; status={f.status}; "
            "R² inclui os efeitos fixos de horário")

    pp = p.set_index("hora_ts")
    for v in sorted(res.versao.unique()):
        s = res[res.versao == v].set_index("hora_ts").qidres.dropna()
        if len(s) < 3:
            lin("D9", f"momentos QIDres [{v}]", "hora", np.nan, (np.nan, np.nan), len(s), "", False, "N < 3")
            continue
        d = pd.DataFrame({"q": s, "dia": pp.loc[s.index, "dia"]})
        d["lag"] = d.groupby("dia").q.shift(1)
        ar = d.dropna()
        ar1 = float(np.corrcoef(ar.q, ar.lag)[0, 1]) if len(ar) > 2 else np.nan
        sd = float(s.std())
        lin("D9", f"DP QIDres [{v}]", "hora", sd, boot_por_dia(d, lambda a: a.q.std(), rng), len(s),
            "Média 0,08; DP 1,54 (Tabela 1)", sd > 2.0 or (np.isfinite(ar1) and abs(ar1) > 0.3),
            f"média={s.mean():.3f}; assimetria={stats.skew(s):.3f}; AR(1) dentro do dia={ar1:.3f} (N pares={len(ar)})")
        if v.endswith("|base"):
            for c in ("pct_spread_tw", "volume", "qvol"):
                dd = d.assign(c=pp.loc[s.index, c].to_numpy())
                rho = float(np.corrcoef(dd.q, dd.c)[0, 1])
                lin("D10", f"ρ(QIDres [{v}], {c})", "hora", rho,
                    boot_por_dia(dd, lambda a: np.corrcoef(a.q, a.c)[0, 1], rng), len(dd),
                    "Próxima de zero (Tabela 1, Painel B)", abs(rho) > 0.3)
    piv = res[res.versao.str.endswith("|base")].pivot_table(index="hora_ts", columns="versao", values="qidres")
    for a_, b_ in (("qid_R|base", "qid_A|base"), ("qid_R|base", "qid_U|base"), ("qid_A|base", "qid_U|base")):
        if a_ in piv and b_ in piv:
            dd = piv[[a_, b_]].dropna()
            dd["dia"] = pp.loc[dd.index, "dia"].to_numpy()
            rho = float(np.corrcoef(dd[a_], dd[b_])[0, 1]) if len(dd) > 2 and dd[b_].std() > 0 else np.nan
            lin("D11", f"ρ({a_}, {b_})", "hora", rho,
                boot_por_dia(dd, lambda x: np.corrcoef(x[a_], x[b_])[0, 1], rng) if np.isfinite(rho) else (np.nan, np.nan),
                len(dd), "Não se aplica", (a_, b_) == ("qid_R|base", "qid_U|base") and np.isfinite(rho) and rho < 0.5)

    out = marcar_u_fraco(pd.DataFrame(L))
    arq = SAIDA / "audit_diagnostics.csv"
    if arq.exists():
        ant = pd.read_csv(arq)
        out = pd.concat([ant[~ant.id.isin(out.id.unique())], out], ignore_index=True)
    out.to_csv(arq, index=False)
    return p, res, fs, out


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 80); pd.set_option("display.max_rows", 200)
    _, _, _, out = main()
    print(out[out.id.isin(["D8", "D9", "D10", "D11"])].drop(columns=["obs"]).to_string(index=False))
