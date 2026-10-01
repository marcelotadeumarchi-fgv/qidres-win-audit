"""
Agregação em QID por período e identidade do spread (D1).
Especificação, seções "Agregação em QID" e "Diagnósticos de auditoria".
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TICK_PONTOS = 5.0


def qid(num: float, det: float) -> float:
    """(num - det)/(num + det); ausente (NaN) se o denominador é zero."""
    den = num + det
    return float("nan") if den == 0 else (num - det) / den


def _delta_spread_segmentos(est: pd.DataFrame, ini: int, fim: int) -> int:
    """Σ (spread_fim − spread_ini) sobre os trechos contínuos do período (vetorizado).

    Estado inicial do período = último estado com t < ini (o último do bloco
    anterior); se não houver, o estado de referência que abre o período. Cada
    novo estado de referência dentro do período abre um novo trecho.
    """
    t = est.t_ms.to_numpy()
    sp = (est.a - est.b).to_numpy()
    ref = est.referencia.to_numpy()
    i0 = int(np.searchsorted(t, ini, side="left"))
    i1 = int(np.searchsorted(t, fim, side="left"))
    refs = i0 + np.flatnonzero(ref[i0:i1])
    inicios = ([i0 - 1] if i0 > 0 else []) + refs.tolist()
    if not inicios:
        return 0
    fins = [r - 1 for r in refs.tolist()] + [i1 - 1]
    if i0 == 0:
        fins = fins[1:] if len(fins) > len(inicios) else fins
    total = 0
    for a, b in zip(inicios, fins):
        if b >= a:
            total += int(sp[b] - sp[a])
    return total


def identidade_spread(eventos: pd.DataFrame, estados: pd.DataFrame, ini: int, fim: int) -> dict:
    """D1: ΔSpread = Σ ticks de deterioração − Σ ticks de melhoria (bid + ask)."""
    ev = eventos[(eventos.t_ms >= ini) & (eventos.t_ms < fim)] if len(eventos) else eventos
    d_spread = _delta_spread_segmentos(estados, ini, fim)
    if len(ev):
        s_det = int(ev.loc[ev.tipo == "deterioracao", "ticks"].sum())
        s_imp = int(ev.loc[ev.tipo == "melhoria", "ticks"].sum())
    else:
        s_det = s_imp = 0
    return {"delta_spread_ticks": int(d_spread), "soma_ticks_det": s_det,
            "soma_ticks_impr": s_imp, "d1_residuo": int(d_spread - (s_det - s_imp))}


def contagens(ev: pd.DataFrame, lado: str | None = None) -> dict:
    if lado is not None and len(ev):
        ev = ev[ev.lado == lado]
    vazio = len(ev) == 0
    imp = ev[ev.tipo == "melhoria"] if not vazio else ev
    det = ev[ev.tipo == "deterioracao"] if not vazio else ev
    n = lambda df, col, val: int((df[col] == val).sum()) if len(df) and col in df else 0
    c = {
        "n_impr": len(imp),
        "n_undercut": n(imp, "classe", "undercut"),
        "n_refill": n(imp, "classe", "recomposicao"),
        "n_shift": n(imp, "classe", "deslocamento"),
        "n_det_all": len(det),
        "n_det_exec": n(det, "causa_A", "execucao"),
        "n_det_cancel": n(det, "causa_A", "cancelamento"),
        "n_det_trade10ms": int(det["r_10ms"].sum()) if len(det) else 0,
        "n_det_trade1ms": int(det["r_1ms"].sum()) if len(det) else 0,
        "n_det_trade10ms_r2": int(det["r2_10ms"].sum()) if len(det) else 0,
        "n_det_trade10ms_semrlp": int(det["r_semrlp_10ms"].sum()) if len(det) else 0,
    }
    for w in (100, 1000, 5000):
        col = f"classe_w{w}"
        c[f"n_undercut_w{w}"] = n(imp, col, "undercut")
        c[f"n_refill_w{w}"] = n(imp, col, "recomposicao")
        c[f"n_shift_w{w}"] = n(imp, col, "deslocamento")
    tr = det[det["r_10ms"]] if len(det) else det
    c["share_1tick_impr"] = float((imp.ticks == 1).mean()) if len(imp) else float("nan")
    c["share_1tick_det_trade"] = float((tr.ticks == 1).mean()) if len(tr) else float("nan")
    return c


def qids(c: dict, cb: dict, ca: dict) -> dict:
    return {
        "qid_R": qid(c["n_impr"], c["n_det_trade10ms"]),
        "qid_R_1ms": qid(c["n_impr"], c["n_det_trade1ms"]),
        "qid_R2": qid(c["n_impr"], c["n_det_trade10ms_r2"]),
        "qid_R_semrlp": qid(c["n_impr"], c["n_det_trade10ms_semrlp"]),
        "qid_A": qid(c["n_impr"], c["n_det_exec"]),
        "qid_U": qid(c["n_undercut"], c["n_det_exec"]),
        # um lado só, definição do artigo (versão R)
        "bqid_R": qid(cb["n_impr"], cb["n_det_trade10ms"]),
        "aqid_R": qid(ca["n_impr"], ca["n_det_trade10ms"]),
        # um lado só, versão A (diagnóstico adicional)
        "bqid_A": qid(cb["n_impr"], cb["n_det_exec"]),
        "aqid_A": qid(ca["n_impr"], ca["n_det_exec"]),
    }


def _sobreposicao(t0: np.ndarray, t1: np.ndarray, exclusoes) -> np.ndarray:
    o = np.zeros(len(t0))
    for a, b in exclusoes or []:
        o += np.clip(np.minimum(t1, b) - np.maximum(t0, a), 0, None)
    return o


def variaveis_estado(estados: pd.DataFrame, negocios: pd.DataFrame, ini: int, fim: int,
                     exclusoes=None, base_pts: float = 0.0) -> dict:
    """Spread ponderado pelo tempo, spread relativo, fração em 1 tick, volume, qvol.

    Cada estado vigora de t_j até t_{j+1} (cortado em [ini, fim)). O estado que
    abre o período é o último antes de `ini`.
    """
    est = estados if estados.t_ms.is_monotonic_increasing else estados.sort_values("t_ms")
    antes = est[est.t_ms < ini].tail(1)
    dentro = est[(est.t_ms >= ini) & (est.t_ms < fim)]
    s = pd.concat([antes, dentro])
    out = {"spread_tw_ticks": float("nan"), "pct_spread_tw": float("nan"),
           "frac_1tick": float("nan"), "qvol": float("nan"),
           "spread_ini_ticks": float("nan"), "spread_fim_ticks": float("nan")}
    if len(s):
        t = np.maximum(s.t_ms.to_numpy(), ini)
        t_fim = np.append(t[1:], fim)
        dur = (t_fim - t).astype(float) - _sobreposicao(t, t_fim, exclusoes)
        sp = (s.a - s.b).to_numpy().astype(float)
        # preços em PONTOS (base + tick·5); spread relativo e retornos exigem preço, não índice de tick
        mid = base_pts + (s.a + s.b).to_numpy() / 2.0 * TICK_PONTOS
        tot = dur.sum()
        if tot > 0:
            out["spread_tw_ticks"] = float((sp * dur).sum() / tot)
            out["pct_spread_tw"] = float(((sp * TICK_PONTOS / mid) * dur).sum() / tot)
            out["frac_1tick"] = float(dur[sp == 1].sum() / tot)
        out["spread_ini_ticks"] = float(sp[0])
        out["spread_fim_ticks"] = float(sp[-1])
        # qvol: DP dos retornos de 1 minuto pelo midpoint (amostrado no fim de cada minuto)
        grade = np.arange(ini + 60_000, fim + 1, 60_000)
        if len(grade) >= 3:
            idx = np.searchsorted(s.t_ms.to_numpy(), grade, side="right") - 1
            ok = idx >= 0
            m = mid[idx[ok]]
            r = np.diff(np.log(m))
            out["qvol"] = float(np.std(r, ddof=1)) if len(r) >= 2 else float("nan")
    v = negocios[(negocios.t_ms >= ini) & (negocios.t_ms < fim)] if len(negocios) else negocios
    for a, b in exclusoes or []:
        if len(v):
            v = v[~((v.t_ms >= a) & (v.t_ms < b))]
    out["volume"] = int(v.volume.sum()) if len(v) else 0
    return out


def agregar_periodo(eventos: pd.DataFrame, estados: pd.DataFrame, negocios: pd.DataFrame,
                    ini: int, fim: int, rotulo: dict, exclusoes=None, base_pts: float = 0.0) -> dict:
    ev = eventos[(eventos.t_ms >= ini) & (eventos.t_ms < fim)] if len(eventos) else eventos
    c = contagens(ev)
    cb, ca = contagens(ev, "bid"), contagens(ev, "ask")
    linha = dict(rotulo)
    linha.update(c)
    linha.update({f"bid_{k}": v for k, v in cb.items() if k.startswith("n_")})
    linha.update({f"ask_{k}": v for k, v in ca.items() if k.startswith("n_")})
    linha.update(qids(c, cb, ca))
    linha.update(identidade_spread(ev, estados, ini, fim))
    # tamanhos médios em ticks (rodada 2): m_I das melhorias, m_D das deteriorações (todas as causas)
    linha["m_I"] = linha["soma_ticks_impr"] / c["n_impr"] if c["n_impr"] else float("nan")
    linha["m_D"] = linha["soma_ticks_det"] / c["n_det_all"] if c["n_det_all"] else float("nan")
    linha.update(variaveis_estado(estados, negocios, ini, fim, exclusoes, base_pts))
    linha["theta"] = (c["n_det_exec"] / c["n_det_all"]) if c["n_det_all"] else float("nan")
    th = linha["theta"]
    linha["qid_composicao"] = (1 - th) / (1 + th) if not math.isnan(th) else float("nan")
    linha["qid_ausente"] = math.isnan(linha["qid_R"])
    return linha
