"""
Contagem e classificação de eventos (especificação, seção "Contagem de eventos
no MBO"). Entrada: a sequência de `Estado` produzida por `LivroMBO.aplicar`.
Saída: um registro por melhoria ou deterioração (events_classified) e a série
de estados válidos (para spread ponderado pelo tempo e para D1).

Convenções de sinal: d > 0 é melhoria. Bid: d = b_k - b_{k-1}. Ask: d = a_{k-1} - a_k.

Interpretações registradas (vão para run_log.md, ver PERGUNTAS em relatorio):
  I1  "maior melhor bid observado em (t-W, t)": máximo sobre os estados
      válidos ANTERIORES ao evento k cujo intervalo de vigência
      [t_j, t_{j+1}] termina depois de t-W. Um nível que vigorou até dentro da
      janela foi "observado" nela, mesmo que tenha começado antes de t-W.
  I2  "o ask sofreu deterioração em (t-W, t)": deteriorações do ask em eventos
      com t_j > t-W, INCLUINDO o próprio evento k (movimento simultâneo).
  I3  Versão R: "primeira deterioração em até 10 ms após um negócio" -- o
      negócio do próprio evento conta (Δt = 0); cada negócio habilita no máximo
      uma deterioração por lado; um negócio novo reabre a janela.
  I4  Históricos (janelas W e de 10 ms) são zerados no estado de referência:
      nada observado em leilão ou janela de exclusão entra na classificação.
"""
from __future__ import annotations

from collections import deque

from .livro import ASK, BID, Estado, LivroMBO

JANELAS_W_MS = (100, 1000, 5000)
W_PADRAO_MS = 1000
JANELAS_R_MS = (10, 1)


class Contador:
    def __init__(self, livro: LivroMBO, dia: str = "", janelas_w=JANELAS_W_MS,
                 janelas_r=JANELAS_R_MS):
        self.livro = livro
        self.dia = dia
        self.jw = tuple(janelas_w)
        self.jr = tuple(janelas_r)
        self.wmax = max(self.jw)
        self.eventos: list[dict] = []
        # estados válidos em colunas tipadas (array) -- ~17 milhões por pregão;
        # dicionários por estado custavam vários GB. pd.DataFrame(self.estados) funciona igual.
        from array import array
        self.estados = {"dia": [], "t_ms": array("q"), "seq": array("q"), "k": array("q"),
                        "b": array("q"), "a": array("q"), "qb": array("q"), "qa": array("q"),
                        "referencia": array("b"), "volume": array("q")}
        self.n_conflito_causa = 0     # execução no evento E último redutor = cancelamento
        self.negocios: list[tuple[int, int]] = []   # (t_ms, volume) no pregão contínuo
        self._reset()

    def estados_df(self):
        import pandas as pd
        df = pd.DataFrame({k: (v if isinstance(v, list) else __import__("numpy").frombuffer(v, dtype="int8" if k == "referencia" else "int64"))
                           for k, v in self.estados.items()})
        df["referencia"] = df["referencia"].astype(bool)
        return df

    # ------------------------------------------------------------------ util
    def _reset(self):
        self.ant: Estado | None = None          # último estado válido contado
        self.hist: deque = deque()              # (t_ini, b, a) dos estados válidos
        self.det_t = [deque(), deque()]         # tempos das deteriorações por lado
        # versão R: último negócio (qualquer / iniciado por venda / por compra)
        self.t_neg = None
        self.t_neg_nrl = None
        self.usado = {}                         # (janela, lado, variante) -> bool
        self.t_neg_lado = {+1: None, -1: None}
        self.precisa_ref = True

    def _podar(self, t):
        # guarda só o que ainda pode cair na maior janela W
        h = self.hist
        while len(h) >= 2 and h[1][0] <= t - self.wmax:
            h.popleft()
        for dq in self.det_t:
            while dq and dq[0] <= t - self.wmax:
                dq.popleft()

    def _max_bid_min_ask(self, t, w):
        """I1: extremos sobre estados anteriores vigentes depois de t-w."""
        mb, ma = None, None
        h = self.hist
        for i, (ti, b, a) in enumerate(h):
            t_fim = h[i + 1][0] if i + 1 < len(h) else t
            assert ti <= t, "leitura de futuro no histórico de estados"
            if t_fim > t - w:
                mb = b if mb is None else max(mb, b)
                ma = a if ma is None else min(ma, a)
        return mb, ma

    def _classe_melhoria(self, lado, p, t, w):
        mb, ma = self._max_bid_min_ask(t, w)
        if lado == BID:
            if mb is not None and p <= mb:
                return "recomposicao"
        else:
            if ma is not None and p >= ma:
                return "recomposicao"
        oposto = ASK if lado == BID else BID
        if any(t - w < td <= t for td in self.det_t[oposto]):
            return "deslocamento"
        return "undercut"

    # ------------------------------------------------------------- principal
    def processar(self, seq: int, t: int, fase: str, msgs: list) -> Estado:
        """Evento de um pacote só (testes). Dados reais: pipeline usa processar_estado."""
        assert self.ant is None or t >= self.ant.t, "evento fora de ordem temporal"
        return self.processar_estado(self.livro.aplicar(seq, t, fase, msgs))

    def processar_estado(self, e: Estado) -> Estado:
        assert self.ant is None or e.t >= self.ant.t, "evento fora de ordem temporal"
        fase = e.fase
        if fase != "C":
            # leilão / janela excluída: book atualizado, nada contado
            self._reset()
            return e
        if e.volume:
            self.negocios.append((e.t, e.volume))
        if not e.valido:
            return e
        if self.precisa_ref:
            # I4 + caso de borda: primeiro estado válido = só referência
            self.precisa_ref = False
            self._registrar_negocio(e)
            self._fechar_estado(e, referencia=True)
            return e

        ant = self.ant
        self._podar(e.t)
        # negócio do próprio evento entra antes das deteriorações (I3)
        self._registrar_negocio(e)

        # I2: deteriorações do evento são registradas ANTES de classificar as
        # melhorias, para que um movimento simultâneo dos dois lados no mesmo
        # evento (ex.: agressão que esvazia o ask e deixa o resíduo como bid)
        # seja visto pela regra de deslocamento.
        mudancas = []
        for lado in (BID, ASK):
            if lado == BID:
                d, p, p_ant = e.b - ant.b, e.b, ant.b
            else:
                d, p, p_ant = ant.a - e.a, e.a, ant.a
            assert d == int(d), "variação não inteira em ticks: erro de reconstrução"
            if d != 0:
                mudancas.append((lado, int(d), p, p_ant))
        regs = {}
        for lado, d, p, p_ant in mudancas:
            if d < 0:
                reg = self._registro_base(e, ant, lado, -d)
                reg["tipo"] = "deterioracao"
                reg["causa_A"] = self._causa_A(lado, p_ant, ant)
                for j in self.jr:
                    reg[f"r_{j}ms"] = self._r_trade(lado, e.t, j, None)
                    reg[f"r_semrlp_{j}ms"] = self._r_trade(lado, e.t, j, "nrl")
                    reg[f"r2_{j}ms"] = self._r_trade(lado, e.t, j,
                                                     -1 if lado == BID else +1)
                self.det_t[lado].append(e.t)
                regs[lado] = reg
        for lado, d, p, p_ant in mudancas:
            if d > 0:
                reg = self._registro_base(e, ant, lado, d)
                reg["tipo"] = "melhoria"
                for w in self.jw:
                    reg[f"classe_w{w}"] = self._classe_melhoria(lado, p, e.t, w)
                reg["classe"] = reg[f"classe_w{W_PADRAO_MS}"]
                regs[lado] = reg
        for lado in (BID, ASK):
            if lado in regs:
                self.eventos.append(regs[lado])

        self._fechar_estado(e, referencia=False)
        return e

    # ------------------------------------------------------------ auxiliares
    def _registrar_negocio(self, e: Estado):
        if not e.teve_negocio:
            return
        self.t_neg = e.t
        for k in [k for k in self.usado if k[2] == "qualquer"]:
            self.usado[k] = False
        if e.teve_negocio_nrl:
            self.t_neg_nrl = e.t
            for k in [k for k in self.usado if k[2] == "nrl"]:
                self.usado[k] = False
        if e.agressor in (+1, -1):
            self.t_neg_lado[e.agressor] = e.t
            v = "venda" if e.agressor == -1 else "compra"
            for k in [k for k in self.usado if k[2] == v]:
                self.usado[k] = False

    def _r_trade(self, lado, t, janela, agressor_exigido):
        """I3. None = qualquer negócio (R); 'nrl' = negócio não RLP; -1/+1 = R2."""
        if agressor_exigido is None:
            t0, var = self.t_neg, "qualquer"
        elif agressor_exigido == "nrl":
            t0, var = self.t_neg_nrl, "nrl"
        else:
            t0 = self.t_neg_lado[agressor_exigido]
            var = "venda" if agressor_exigido == -1 else "compra"
        if t0 is None:
            return False
        assert t0 <= t, "negócio no futuro usado na versão R"
        chave = (janela, lado, var)
        if t - t0 <= janela and not self.usado.get(chave, False):
            self.usado[chave] = True
            return True
        return False

    def _causa_A(self, lado, p_ant, ant: Estado):
        lv = self.livro
        assert lv.cnt[lado][p_ant] == 0, "deterioração sem esvaziar o nível anterior"
        ez = lv.esvaz[lado].get(p_ant)
        assert ez is not None and ez.evento > ant.k, \
            "nível anterior não foi esvaziado depois do estado anterior"
        if ez.exec_no_evento:
            if ez.ultimo_redutor != "exec":
                self.n_conflito_causa += 1
            return "execucao"
        assert ez.ultimo_redutor == "cancel"
        return "cancelamento"

    def _registro_base(self, e: Estado, ant: Estado, lado, ticks):
        return {
            "dia": self.dia, "t_ms": e.t, "seq": e.seq, "k": e.k,
            "lado": "bid" if lado == BID else "ask", "ticks": ticks,
            # estado em t- (antes) e em t (depois)
            "b_ant": ant.b, "a_ant": ant.a, "qb_ant": ant.qb, "qa_ant": ant.qa,
            "spread_ant": ant.a - ant.b,
            "b": e.b, "a": e.a, "qb": e.qb, "qa": e.qa,
            "teve_negocio": e.teve_negocio, "teve_negocio_nrl": e.teve_negocio_nrl,
            "agressor": e.agressor, "n_pacotes": e.n_pacotes, "saldo_final": e.saldo_final,
            "linha_ini": getattr(e, "linha_ini", -1), "linha_fim": getattr(e, "linha_fim", -1),
        }

    def _fechar_estado(self, e: Estado, referencia: bool):
        self.hist.append((e.t, e.b, e.a))
        E = self.estados
        E["dia"].append(self.dia)
        for c, v in (("t_ms", e.t), ("seq", e.seq), ("k", e.k), ("b", e.b), ("a", e.a),
                     ("qb", e.qb), ("qa", e.qa), ("referencia", int(referencia)), ("volume", e.volume)):
            E[c].append(v)
        self.ant = e
