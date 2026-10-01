"""
Os 8 testes sintéticos da especificação (seção "Saídas, testes unitários e
ordem de execução"), mais testes das asserções de integridade.

Rodar:  python -m auditoria_qidres.testes.test_sinteticos   (a partir de b3_data/qid)
O pipeline não avança se algum falhar (código de saída != 0).
"""
from __future__ import annotations

import random
import sys
import traceback

import numpy as np
import pandas as pd

from ..agregacao import agregar_periodo, identidade_spread
from ..contagem import Contador
from ..livro import ASK, BID, LivroMBO, para_tick
from ..regressao import qidres_por_janela, qidres_trimestral

N_TICKS = 400


class Sim:
    """Gera eventos de casamento sintéticos no formato do feed (D/C/N/T por msg_seq_num)."""

    def __init__(self):
        self.cont = Contador(LivroMBO(N_TICKS), dia="sint")
        self.oid = 0
        self.seq = 0
        self.vivas: dict[int, list] = {}      # oid -> [lado, tick, qtd]

    def _env(self, t, msgs, fase="C"):
        self.seq += 1
        return self.cont.processar(self.seq, t, fase, msgs)

    def evento_multipacote(self, t, pacotes, fase="C"):
        """Um evento de casamento publicado em vários pacotes (desvio D-1)."""
        lv = self.cont.livro
        self.seq += 1
        lv.iniciar(self.seq, t, fase)
        for p in pacotes:
            lv.pacote(p)
        return self.cont.processar_estado(lv.fechar())

    def nova(self, t, lado, tick, q=1, fase="C"):
        self.oid += 1
        self.vivas[self.oid] = [lado, tick, q]
        self._env(t, [("N", lado, tick, q, self.oid)], fase)
        return self.oid

    def cancela(self, t, oid, fase="C"):
        lado, tick, q = self.vivas.pop(oid)
        self._env(t, [("D", lado, tick, q, oid)], fase)

    def agride(self, t, lado_passivo, qtd, fase="C"):
        """Ordem a mercado contra `lado_passivo`: consome por prioridade preço-tempo."""
        msgs_t, msgs_b = [], []
        resto = qtd
        while resto > 0:
            cand = [(o, v) for o, v in self.vivas.items() if v[0] == lado_passivo]
            if not cand:
                break
            alvo = max(cand, key=lambda x: x[1][1]) if lado_passivo == BID else min(cand, key=lambda x: x[1][1])
            tick_alvo = alvo[1][1]
            fila = sorted([c for c in cand if c[1][1] == tick_alvo], key=lambda c: c[0])
            for o, v in fila:
                if resto == 0:
                    break
                f = min(resto, v[2])
                msgs_t.append(("T", -1, tick_alvo, f, None))
                resto -= f
                if f == v[2]:
                    msgs_b.append(("D", lado_passivo, tick_alvo, v[2], o))
                    self.vivas.pop(o)
                else:
                    v[2] -= f
                    msgs_b.append(("C", lado_passivo, tick_alvo, v[2], o))
        self._env(t, msgs_t + msgs_b, fase)

    def df(self):
        ev = pd.DataFrame(self.cont.eventos)
        es = self.cont.estados_df()
        ng = pd.DataFrame(self.cont.negocios, columns=["t_ms", "volume"])
        return ev, es, ng


def _abre(sim, bid, ask, t=0, q=1):
    ob = sim.nova(t, BID, bid, q)
    oa = sim.nova(t + 1, ASK, ask, q)       # aqui o estado fica válido -> referência
    return ob, oa


# ------------------------------------------------------------------- testes
def teste_1_corrida_limpa():
    s = Sim()
    s.nova(0, BID, 99, 3)
    _abre(s, 100, 105, t=1)
    for i, p in enumerate((101, 102, 103)):
        s.nova(1000 * (i + 1), BID, p, 2)
    s.agride(4000, BID, 2)                 # venda esvazia o melhor bid (103)
    ev, es, ng = s.df()
    imp, det = ev[ev.tipo == "melhoria"], ev[ev.tipo == "deterioracao"]
    assert len(imp) == 3 and (imp.ticks == 1).all(), imp
    assert (imp.classe == "undercut").all(), imp.classe.tolist()
    assert len(det) == 1 and det.iloc[0].causa_A == "execucao" and det.iloc[0].lado == "bid"
    linha = agregar_periodo(ev, es, ng, 0, 10_000, {"p": 1})
    assert abs(linha["qid_A"] - 0.5) < 1e-12, linha["qid_A"]
    assert abs(linha["qid_R"] - 0.5) < 1e-12, linha["qid_R"]
    assert abs(linha["qid_U"] - 0.5) < 1e-12, linha["qid_U"]
    assert linha["d1_residuo"] == 0
    return (f"3 melhorias {imp.classe.tolist()}, 1 det. {det.iloc[0].causa_A}; "
            f"QID_A={linha['qid_A']:.3f} QID_R={linha['qid_R']:.3f} QID_U={linha['qid_U']:.3f}")


def teste_2_recomposicao():
    s = Sim()
    s.nova(0, BID, 99, 3)
    _abre(s, 100, 101, t=1)
    s.agride(1000, BID, 1)                 # venda esvazia o bid 100
    s.nova(1200, BID, 100, 1)              # nova ordem volta a 100, 200 ms depois
    ev, _, _ = s.df()
    imp, det = ev[ev.tipo == "melhoria"], ev[ev.tipo == "deterioracao"]
    assert len(det) == 1 and det.iloc[0].causa_A == "execucao"
    assert len(imp) == 1 and imp.iloc[0].classe == "recomposicao", imp.classe.tolist()
    assert (ev.get("classe") == "undercut").sum() == 0
    return (f"1 det. {det.iloc[0].causa_A}; 1 melhoria '{imp.iloc[0].classe}'; 0 undercuts "
            f"(W=100ms: {imp.iloc[0].classe_w100}, W=5s: {imp.iloc[0].classe_w5000})")


def teste_3_deslocamento():
    s = Sim()
    _abre(s, 100, 101, t=1)
    s.nova(2, ASK, 102, 3)                 # profundidade atrás do melhor ask: sem evento
    s.agride(1000, ASK, 1)                 # compra esvazia o ask 101
    s.nova(1300, BID, 101, 1)              # bid sobe 1 tick 300 ms depois
    ev, _, _ = s.df()
    det = ev[ev.tipo == "deterioracao"]
    imp = ev[ev.tipo == "melhoria"]
    assert len(det) == 1 and det.iloc[0].lado == "ask" and det.iloc[0].causa_A == "execucao"
    assert len(imp) == 1 and imp.iloc[0].lado == "bid" and imp.iloc[0].classe == "deslocamento", \
        imp.to_dict("records")
    return (f"1 det. ask {det.iloc[0].causa_A}; 1 melhoria bid '{imp.iloc[0].classe}' "
            f"(W=100ms: {imp.iloc[0].classe_w100})")


def teste_4_varredura():
    s = Sim()
    s.nova(0, ASK, 104, 1)
    s.nova(0, ASK, 103, 1)
    s.nova(0, ASK, 102, 1)
    s.nova(0, ASK, 101, 1)
    s.nova(0, BID, 99, 3)                  # estado válido (bid 99, ask 101) -> referência
    s.agride(1000, ASK, 3)                 # consome 101, 102, 103 num único evento
    ev, es, _ = s.df()
    assert len(ev) == 1, ev
    r = ev.iloc[0]
    assert r.tipo == "deterioracao" and r.lado == "ask" and r.ticks == 3 and r.causa_A == "execucao"
    assert identidade_spread(ev, es, 0, 10_000)["d1_residuo"] == 0
    return f"1 deterioração de {r.ticks} ticks no ask, causa {r.causa_A}"


def teste_5_cancelamento():
    out = []
    for atraso, esperado_r in ((None, False), (5, True), (50, False)):
        s = Sim()
        _abre(s, 100, 101, t=1, q=1)
        s.nova(2, BID, 99, 3)
        s.nova(2, ASK, 102, 5)
        s.nova(2, ASK, 101, 4)
        if atraso is not None:
            s.agride(1000 - atraso, ASK, 1)    # negócio que NÃO esvazia o ask
        oid_bid = [o for o, v in s.vivas.items() if v[0] == BID and v[1] == 100][0]
        s.cancela(1000, oid_bid)               # único lote do melhor bid
        ev, _, _ = s.df()
        det = ev[ev.tipo == "deterioracao"]
        assert len(det) == 1 and det.iloc[0].causa_A == "cancelamento", det
        assert bool(det.iloc[0].r_10ms) is esperado_r, (atraso, det.iloc[0].r_10ms)
        assert bool(det.iloc[0].r2_10ms) is False      # negócio foi de compra, não de venda
        out.append(f"negócio {'nenhum' if atraso is None else f'{atraso} ms antes'} -> R={det.iloc[0].r_10ms}")
    return "1 det. cancelamento em todos; " + "; ".join(out)


def _sequencia_aleatoria(rng: random.Random, n_ev: int, com_leilao: bool):
    s = Sim()
    t = 0
    for p in range(195, 200):
        s.nova(t, BID, p, rng.randint(1, 5))
    for p in range(201, 206):
        s.nova(t, ASK, p, rng.randint(1, 5))
    leilao_ini = rng.randint(n_ev // 3, n_ev // 2) if com_leilao else -1
    for i in range(n_ev):
        t += rng.choice([0, 0, 1, 2, 5, 20, 150, 900])
        fase = "L" if leilao_ini <= i < leilao_ini + 15 else "C"
        acao = rng.random()
        b, a = s.cont.livro.best
        lado = rng.choice([BID, ASK])
        if acao < 0.45:
            if lado == BID:
                topo = (a - 1) if a < N_TICKS else 250
                p = rng.randint(max(1, topo - 6), topo)
            else:
                base = (b + 1) if b >= 0 else 150
                p = rng.randint(base, min(N_TICKS - 2, base + 6))
            s.nova(t, lado, p, rng.randint(1, 4), fase)
        elif acao < 0.75 and s.vivas:
            s.cancela(t, rng.choice(list(s.vivas)), fase)
        elif any(v[0] == lado for v in s.vivas.values()):
            s.agride(t, lado, rng.randint(1, 8), fase)
    return s


def teste_6_identidade():
    rng = random.Random(20261001)
    n_seq = n_per = n_ev = 0
    for i in range(300):
        s = _sequencia_aleatoria(rng, rng.randint(50, 400), com_leilao=(i % 3 == 0))
        ev, es, _ = s.df()
        if es.empty:
            continue
        fim = int(es.t_ms.max()) + 1
        cortes = sorted({0, fim} | {rng.randint(0, fim) for _ in range(6)})
        for ini, f in zip(cortes[:-1], cortes[1:]):
            r = identidade_spread(ev, es, ini, f)
            assert r["d1_residuo"] == 0, (i, ini, f, r)
            n_per += 1
        n_seq += 1
        n_ev += len(ev)
    return f"{n_seq} sequências, {n_per} períodos, {n_ev} eventos classificados: resíduo D1 = 0 em todos"


def teste_7_leilao():
    s = Sim()
    s.nova(0, BID, 99, 3)
    _abre(s, 100, 101, t=1)
    # leilão: o book muda bastante, mas nada pode ser contado
    s.nova(10_000, BID, 110, 2, fase="L")
    s.nova(10_001, ASK, 112, 2, fase="L")
    s.nova(10_002, ASK, 111, 2, fase="L")
    s.cancela(10_003, [o for o, v in s.vivas.items() if v[1] == 101][0], fase="L")
    # retomada: primeiro estado contínuo é só referência
    s.nova(20_000, BID, 98, 1)
    s.agride(22_000, ASK, 2)               # esvazia o ask 111 -> deterioração contável
    ev, es, _ = s.df()
    assert not ((ev.t_ms >= 10_000) & (ev.t_ms < 20_001)).any(), ev
    ref = es[es.referencia]
    assert len(ref) == 2 and ref.iloc[1].t_ms == 20_000, ref
    assert len(ev) == 1 and ev.iloc[0].t_ms == 22_000 and ev.iloc[0].lado == "ask"
    assert identidade_spread(ev, es, 0, 30_000)["d1_residuo"] == 0
    return (f"0 eventos no leilão nem no 1º estado posterior (referência em t=20000, "
            f"bid {ref.iloc[1].b}/ask {ref.iloc[1].a}); 1 evento depois; D1 = 0")


def _painel_8(a, b, sig, sd_x, semente):
    rng = np.random.default_rng(semente)
    trims = pd.period_range("2022Q1", "2026Q4", freq="Q")
    linhas = []
    for q in trims:
        dias = pd.bdate_range(q.start_time, q.end_time)
        x = rng.normal(-8.0, sd_x, len(dias))              # ln(%spread)
        linhas += [{"dia": d, "ln_pct_spread": xi, "qid": a + b * xi + rng.normal(0, sig)}
                   for d, xi in zip(dias, x)]
    df = pd.DataFrame(linhas)
    res, fs = qidres_trimestral(df, "qid", ["ln_pct_spread"], rotulo="teste8")
    ok = fs[fs.status == "ok"]
    assert res.trimestre.min() == str(trims[1])           # 1º trimestre só estima
    za = (ok.a - a) / ok.se_a
    zb = (ok.b_ln_pct_spread - b) / ok.se_b_ln_pct_spread
    # cobertura do IC de 95% por trimestre (esperado ~95%) e viés médio dentro de 3 EP
    cob_b = float((zb.abs() < 1.96).mean())
    assert cob_b >= 0.80, cob_b
    assert abs(zb.mean()) < 3 / np.sqrt(len(zb)), zb.mean()
    assert abs(za.mean()) < 3 / np.sqrt(len(za)), za.mean()
    r = res.qidres.dropna()
    r2_pop = b**2 * sd_x**2 / (b**2 * sd_x**2 + sig**2)
    dp_teor = np.sqrt(1 - r2_pop)
    assert abs(r.mean()) < 3 * r.std() / np.sqrt(len(r)) + 0.05, r.mean()
    assert abs(r.std() - dp_teor) < 0.06, (r.std(), dp_teor)
    return ok, r, r2_pop, dp_teor, cob_b


def teste_8_qidres():
    """(a) sinal forte, R² ~ 0,47 como na Fig. E.1: recuperação de a e b precisa.
    (b) sinal fraco, R² ~ 0,04: o caso em que DP(QIDres) ~ 1.
    Por construção, DP(QIDres) = sqrt(1 - R²): com S(QID) = DP do QID (e não
    do resíduo), o DP só é ~1 quando o controle explica pouco. Ver PERGUNTA 6."""
    msgs = []
    for nome, (a, b, sig, sd_x) in {"forte": (0.30, 0.40, 0.10, 0.25),
                                    "fraco": (0.30, 0.08, 0.10, 0.25)}.items():
        ok, r, r2, dp_t, cob = _painel_8(a, b, sig, sd_x, semente=8)
        if nome == "fraco":
            assert abs(r.std() - 1) < 0.10, r.std()
        msgs.append(
            f"[{nome}] {len(ok)} trim.; b̂ médio={ok.b_ln_pct_spread.mean():.3f} (verd. {b}, "
            f"cobertura IC95 por trim.={cob:.0%}); â médio={ok.a.mean():.3f} (verd. {a}); "
            f"QIDres N={len(r)} média={r.mean():+.3f} DP={r.std():.3f} "
            f"(teórico sqrt(1-R²)={dp_t:.3f}, R²={r2:.2f})")
    return "\n         ".join(msgs)


# ------------------------------------------------- asserções de integridade
def teste_extra_tick_nao_inteiro():
    assert para_tick(147265.0, 147000.0) == 53
    try:
        para_tick(147262.5, 147000.0)
    except AssertionError:
        return "preço fora da grade de 5 pontos interrompe (AssertionError)"
    raise AssertionError("preço fora da grade não foi detectado")


def teste_extra_fora_de_ordem():
    s = Sim()
    _abre(s, 100, 101, t=1000)
    s.nova(1500, BID, 99, 1)
    try:
        s.nova(900, BID, 98, 1)
    except AssertionError:
        return "evento com t anterior ao último estado interrompe (sem leitura de futuro)"
    raise AssertionError("evento fora de ordem não foi detectado")


def teste_extra_regressao_sem_futuro():
    df = pd.DataFrame({"dia": pd.bdate_range("2025-01-01", "2025-12-31")})
    rng = np.random.default_rng(0)
    df["x"] = rng.normal(-8, 0.3, len(df))
    df["qid"] = 0.2 + 0.05 * df.x + rng.normal(0, 0.1, len(df))
    res, fs = qidres_trimestral(df, "qid", ["x"])
    assert (pd.PeriodIndex(res.trimestre_param, freq="Q") < pd.PeriodIndex(res.trimestre, freq="Q")).all()
    # S(QID) é o DP dos QIDs, não dos resíduos
    q1 = df[pd.to_datetime(df.dia).dt.quarter == 1].qid
    assert abs(fs.iloc[0].S_QID - q1.std(ddof=1)) < 1e-12
    return "parâmetros sempre do trimestre anterior; S(QID) = DP dos QIDs diários de q−1"


def teste_extra_variancia_zero():
    df = pd.DataFrame({"dia": pd.bdate_range("2025-01-01", "2025-06-30")})
    df["x"] = -8.0                         # controle constante (ex.: spread em reais no WIN)
    df["qid"] = np.linspace(0, 1, len(df))
    res, fs = qidres_trimestral(df, "qid", ["x"])
    assert (fs.status == "nao_identificada").all() and res.qidres.isna().all()
    return "controle com variância zero -> 'nao_identificada', sem QIDres"


def teste_9_varredura_fragmentada():
    """D-1: prints num pacote, exclusões no seguinte, mesmo sending_time."""
    s = Sim()
    asks = [s.nova(0, ASK, p, 2) for p in (104, 103, 102, 101)]
    s.nova(0, BID, 99, 3)                  # referência: bid 99 / ask 101
    o101, o102, o103 = asks[3], asks[2], asks[1]
    for o in (o101, o102, o103):
        s.vivas.pop(o)
    p1 = [("T", -1, 101, 2, False), ("T", -1, 102, 2, False), ("T", -1, 103, 2, False)]
    p2 = [("D", ASK, 101, 2, o101), ("D", ASK, 102, 2, o102)]
    p3 = [("D", ASK, 103, 2, o103)]
    e = s.evento_multipacote(1000, [p1, p2, p3])
    ev, es, _ = s.df()
    assert e.saldo_final == 0 and e.n_pacotes == 3, (e.saldo_final, e.n_pacotes)
    assert len(ev) == 1 and ev.iloc[0].ticks == 3 and ev.iloc[0].causa_A == "execucao", ev
    # contraprova: os mesmos pacotes tratados como eventos separados (msg_seq_num literal)
    s2 = Sim()
    asks = [s2.nova(0, ASK, p, 2) for p in (104, 103, 102, 101)]
    s2.nova(0, BID, 99, 3)
    s2._env(1000, p1); s2._env(1000, [("D", ASK, 101, 2, asks[3]), ("D", ASK, 102, 2, asks[2])])
    s2._env(1000, [("D", ASK, 103, 2, asks[1])])
    ev2, _, _ = s2.df()
    return (f"evento em 3 pacotes -> 1 deterioração de {ev.iloc[0].ticks} ticks, {ev.iloc[0].causa_A}; "
            f"literal por msg_seq_num daria {len(ev2)} deteriorações com causas {ev2.causa_A.tolist()}")


def teste_11_deslocamento_mesmo_evento():
    """I2: agressão de compra esvazia o ask e o resíduo vira o melhor bid NO MESMO evento."""
    s = Sim()
    _abre(s, 100, 101, t=1, q=2)
    s.nova(2, ASK, 102, 5)
    o_ask = [o for o, v in s.vivas.items() if v[0] == ASK and v[1] == 101][0]
    s.vivas.pop(o_ask)
    s.oid += 1
    s._env(1000, [("T", -1, 101, 2, False), ("D", ASK, 101, 2, o_ask), ("N", BID, 101, 3, s.oid)])
    ev, _, _ = s.df()
    imp = ev[ev.tipo == "melhoria"].iloc[0]
    det = ev[ev.tipo == "deterioracao"].iloc[0]
    assert det.lado == "ask" and det.causa_A == "execucao"
    assert imp.lado == "bid" and imp.classe == "deslocamento", imp.classe
    return f"det. ask {det.causa_A} + melhoria bid '{imp.classe}' no mesmo evento"


def teste_10_rlp():
    """D-2: print RLP não executa ordem do book; conta na versão R, não na variante sem RLP."""
    s = Sim()
    _abre(s, 100, 101, t=1)
    s.nova(2, BID, 99, 3)
    s._env(995, [("T", -1, 101, 3, True)])            # negócio RLP, book intacto
    oid_bid = [o for o, v in s.vivas.items() if v[0] == BID and v[1] == 100][0]
    s.cancela(1000, oid_bid)
    ev, es, _ = s.df()
    det = ev[ev.tipo == "deterioracao"].iloc[0]
    assert det.causa_A == "cancelamento" and bool(det.r_10ms) and not bool(det.r_semrlp_10ms)
    return f"det. {det.causa_A}; R(10ms)={det.r_10ms}, R sem RLP={det.r_semrlp_10ms}"


def teste_extra_unidades_spread():
    """Spread relativo e qvol em PONTOS: 1 tick (5 pts) a ~147.000 pts -> ~3,4e-5."""
    from ..agregacao import variaveis_estado
    es = pd.DataFrame({"t_ms": [0, 60_000, 120_000, 180_000], "b": [100, 100, 101, 100],
                       "a": [101, 101, 102, 101], "referencia": [True, False, False, False]})
    v = variaveis_estado(es, pd.DataFrame(columns=["t_ms", "volume"]), 0, 240_000, base_pts=146_500.0)
    esperado = 5.0 / (146_500.0 + 100.5 * 5)
    assert abs(v["pct_spread_tw"] - esperado) / esperado < 0.01, v["pct_spread_tw"]
    assert v["qvol"] < 1e-3, v["qvol"]
    return f"pct_spread_tw={v['pct_spread_tw']:.3e} (esperado {esperado:.3e}); qvol={v['qvol']:.2e}"


def teste_12_qidres_semanal_hora():
    """Desvio D-3: unidade hora, janela semana anterior, efeitos fixos de horário."""
    rng = np.random.default_rng(12)
    a, b, sig = 0.20, 0.05, 0.03
    fe_h = {h: 0.01 * (h - 9) for h in range(9, 19)}
    linhas = []
    for d in pd.bdate_range("2025-01-06", "2025-06-27"):
        for h in range(9, 19):
            x = rng.normal(-10.3, 0.3)
            linhas.append({"hora": d + pd.Timedelta(hours=h), "h": h,
                           "semana": d.to_period("W").start_time.date().isoformat(),
                           "x": x, "qid": a + b * x + fe_h[h] + rng.normal(0, sig)})
    df = pd.DataFrame(linhas)
    df = df[~((df.semana == "2025-03-03") & (df.h == 18))]           # categoria ausente numa semana
    res, fs = qidres_por_janela(df, "qid", ["x"], "semana", "hora", fe="h", rotulo="t12")
    ok = fs[fs.status == "ok"]
    zb = (ok.b_x - b) / ok.se_b_x
    assert abs(zb.mean()) < 3 / np.sqrt(len(zb)), zb.mean()
    assert (res.janela_param < res.semana).all()
    assert res.semana.min() == sorted(df.semana.unique())[1]          # 1ª semana só estima
    buraco = res[(res.janela_param == "2025-03-03") & (res.h == 18)]
    assert len(buraco) and (buraco.status == "nao_identificada").all()
    r = res.qidres.dropna()
    assert abs(r.mean()) < 0.1
    return (f"{len(ok)} semanas; b̂ médio={ok.b_x.mean():.4f} (verd. {b}); QIDres N={len(r)} "
            f"média={r.mean():+.3f} DP={r.std():.3f}; hora sem categoria na semana anterior -> não identificada")


def teste_r2_tarefa2_funcoes():
    """Tarefa 2: partial R², defasagens sem atravessar lacunas/pregões, sinal da OFI (CKS)."""
    from ..tarefa2_causalidade import ofi_eventos, painel_defasado, partial_r2
    rng = np.random.default_rng(3)
    x, z = rng.normal(size=2000), rng.normal(size=2000)
    y = 0.5 * x + z + rng.normal(size=2000)
    pr, _ = partial_r2(y, np.column_stack([x, z]), z.reshape(-1, 1))
    esperado = 0.25 / (0.25 + 1)
    assert abs(pr - esperado) < 0.04, pr
    df = pd.DataFrame({"dia": ["d1"] * 4 + ["d2"] * 2, "ms": [0, 300_000, 900_000, 1_200_000, 0, 300_000],
                       "v": [1., 2., 3., 4., 5., 6.]})
    L1 = painel_defasado(df, "v", [1])["v_L1"].tolist()
    assert np.isnan(L1[0]) and L1[1] == 1 and np.isnan(L1[2]) and L1[3] == 3 and np.isnan(L1[4]) and L1[5] == 5, L1
    es = pd.DataFrame({"b": [100, 100, 101, 101], "a": [101, 101, 102, 102], "qb": [5, 8, 3, 3],
                       "qa": [4, 4, 6, 2], "referencia": [True, False, False, False]})
    e = ofi_eventos(es)
    # 1: bid +3 (8−5)  2: bid sobe -> +3; ask sobe -> +4 (qa_ant)  3: ask qtd cai 6->2 -> −2+6 = +4
    assert list(e) == [0.0, 3.0, 7.0, 4.0], list(e)
    return f"partial R²={pr:.3f} (esperado {esperado:.3f}); defasagens respeitam lacunas e pregões; OFI={list(e)}"


def teste_r2_tarefa3_funcoes():
    """Tarefa 3: Shapley soma ao R² total; spline natural é linear fora dos nós de fronteira."""
    from ..tarefa3_decomposicao import nos_df4, ns_base, shapley
    rng = np.random.default_rng(4)
    n = 3000
    a, b, c = rng.normal(size=n), rng.normal(size=n), rng.normal(size=n)
    y = np.sin(a) + 0.5 * b ** 2 + 0.3 * a * c + rng.normal(scale=0.5, size=n)
    bases = {g: ns_base(v, nos_df4(v)) for g, v in (("a", a), ("b", b), ("c", c))}
    ph = shapley(y, bases)
    soma = ph["a"] + ph["b"] + ph["c"]
    assert abs(soma - ph["R2_total"]) < 1e-10, (soma, ph["R2_total"])
    nos = nos_df4(a)
    xs = np.linspace(nos[-1] + 0.1, nos[-1] + 3, 50)
    Bx = ns_base(xs, nos)
    assert np.allclose(np.diff(Bx, 2, axis=0), 0, atol=1e-8), "spline não é linear além do último nó"
    assert ns_base(a, nos).shape[1] == 4
    return f"Σφ = {soma:.4f} = R² total; 4 colunas; linear fora da fronteira"


def teste_r3_confiabilidade_e_wild():
    """Rodada 3: Spearman-Brown recupera confiabilidade conhecida; wild cluster (Webb) cobre β verdadeiro."""
    from ..rodada3_viabilidade import spearman_brown, wild_beta, wild_sb
    rng = np.random.default_rng(31)
    G, m, n_meia, sig = 12, 100, 20, 2.0
    cl = np.repeat(np.arange(G), m)
    mu = rng.normal(size=G * m)                                    # sinal verdadeiro do bloco
    x = mu + rng.normal(scale=sig / np.sqrt(n_meia), size=G * m)   # metade ímpar
    y = mu + rng.normal(scale=sig / np.sqrt(n_meia), size=G * m)   # metade par
    r_teor = 1 / (1 + sig ** 2 / n_meia)
    sb_teor = spearman_brown(r_teor)
    sb = spearman_brown(np.corrcoef(x, y)[0, 1])
    ic, _ = wild_sb(x, y, cl, rng, B=999)
    assert ic[0] <= sb_teor <= ic[1] and abs(sb - sb_teor) < 0.05, (sb, sb_teor, ic)
    cobre = 0
    for rep in range(40):
        u = np.repeat(rng.normal(size=G), m) + rng.normal(size=G * m)
        xx = rng.normal(size=G * m) + np.repeat(rng.normal(size=G), m) * 0.5
        yy = 0.3 * xx + u
        X = np.column_stack([np.ones(len(yy)), xx])
        b, ic_b, _ = wild_beta(yy, X, cl, 1, rng, B=499)
        cobre += ic_b[0] <= 0.3 <= ic_b[1]
    assert cobre >= 30, cobre
    return (f"SB={sb:.3f} (teórico {sb_teor:.3f}, IC [{ic[0]:.3f}, {ic[1]:.3f}]); "
            f"cobertura do IC wild (Webb, 12 clusters) para β: {cobre}/40")


TESTES = [teste_1_corrida_limpa, teste_2_recomposicao, teste_3_deslocamento, teste_4_varredura,
          teste_5_cancelamento, teste_6_identidade, teste_7_leilao, teste_8_qidres,
          teste_9_varredura_fragmentada, teste_10_rlp, teste_11_deslocamento_mesmo_evento,
          teste_12_qidres_semanal_hora,
          teste_extra_tick_nao_inteiro, teste_extra_fora_de_ordem,
          teste_extra_regressao_sem_futuro, teste_extra_variancia_zero, teste_extra_unidades_spread,
          teste_r2_tarefa2_funcoes, teste_r2_tarefa3_funcoes,
          teste_r3_confiabilidade_e_wild]


def main() -> int:
    falhas = 0
    for f in TESTES:
        try:
            msg = f()
            print(f"PASSOU  {f.__name__}: {msg}")
        except Exception:
            falhas += 1
            print(f"FALHOU  {f.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTES) - falhas}/{len(TESTES)} testes passaram")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
