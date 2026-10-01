"""
Book do melhor nível por EVENTO DE CASAMENTO (auditoria QIDres, especificação
`auditoria_qidres_win.md`, seção "Contagem de eventos no MBO").

Regras de reconstrução idênticas às de `undercutting_valida.replay_dia`
(mapa order_id -> (tick, qtd); New insere, Change redimensiona, Delete remove;
melhor bid/ask como ponteiros, reescaneados só quando o melhor nível esvazia).
A diferença é a granularidade: aqui o estado é lido ao FIM de cada evento de
casamento, não após cada mensagem. Um evento é um pacote (`msg_seq_num`) mais
os pacotes seguintes com o mesmo `sending_time` enquanto o volume negociado
sem RLP não tiver sido todo executado no book (desvio aprovado D-1, run_log). A equivalência com o replay existente é verificada no passo 2
(`validar_contra_replay`), antes de qualquer uso em dados reais.

Mensagem = tupla (tipo, lado, tick, qtd, oid)
    tipo : 'N' inclusão, 'C' alteração de quantidade, 'D' exclusão, 'T' negócio
           (em 'T' o 5º campo é True se o print é RLP: trade_condition contém 'RL')
    lado : BID=0, ASK=1 (negócio: -1)
    tick : preço em ticks inteiros (preço / 5 pontos)
    qtd  : N -> quantidade; C -> quantidade REMANESCENTE; D -> quantidade
           excluída; T -> quantidade negociada
Evento = (seq, t_ms, fase, [mensagens])
    fase : 'C' pregão contínuo válido; qualquer outro valor (leilão, janela de
           exclusão) -> o book é atualizado, mas nada é contado.

Execução x cancelamento (versão A). O feed não liga o print de negócio à ordem
passiva; a ligação é feita pelo evento de casamento: um D ou C, no mesmo
`msg_seq_num` de um print, no MESMO preço do print, é execução. Todo D fora
disso é cancelamento (ou a metade "cancelamento" de uma modificação de preço,
que a B3 publica como D + N). Um C sem print no evento é anomalia e é contado.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

BID, ASK = 0, 1


@dataclass
class Esvaziamento:
    """Como um nível de preço chegou a zero pela última vez."""
    evento: int          # índice do evento de casamento em que zerou
    exec_no_evento: bool  # houve execução nesse tick, nesse lado, nesse evento
    ultimo_redutor: str  # 'exec' | 'cancel' -- tipo da última mensagem que reduziu


@dataclass
class Estado:
    """Estado do melhor nível ao fim de um evento de casamento."""
    k: int
    seq: int
    t: int
    fase: str
    b: int
    a: int
    qb: int
    qa: int
    valido: bool
    teve_negocio: bool
    agressor: int             # +1 compra, -1 venda, 0 sem negócio/indeterminado
    volume: int
    n_exec_msgs: int = 0
    q_exec: int = 0            # contratos retirados do lado passivo por execução
    teve_negocio_nrl: bool = False   # houve negócio não RLP
    volume_nrl: int = 0
    saldo_final: int = 0       # volume não RLP ainda sem execução correspondente ao fechar
    n_pacotes: int = 1
    anomalias: list = field(default_factory=list)


class LivroMBO:
    def __init__(self, n_ticks: int):
        self.n = n_ticks
        self.cnt = [np.zeros(n_ticks, dtype=np.int32), np.zeros(n_ticks, dtype=np.int32)]
        self.sz = [np.zeros(n_ticks, dtype=np.int64), np.zeros(n_ticks, dtype=np.int64)]
        self.ordens: dict[int, tuple[int, int, int]] = {}   # oid -> (lado, tick, qtd)
        self.best = [-1, n_ticks]                           # -1 / n = lado vazio
        self.esvaz: list[dict[int, Esvaziamento]] = [{}, {}]
        self.k = -1
        self.n_anom = {"change_sem_negocio": 0, "change_aumenta": 0,
                       "delete_orfao": 0, "change_orfao": 0, "oid_duplicado": 0}

    # ---------------------------------------------------------------- melhor
    def _rescan(self, lado: int):
        c = self.cnt[lado]
        if lado == BID:
            j = self.best[BID]
            while j >= 0 and c[j] == 0:
                j -= 1
            self.best[BID] = j
        else:
            j = self.best[ASK]
            while j < self.n and c[j] == 0:
                j += 1
            self.best[ASK] = j

    def _add(self, lado, tick, q):
        if not (0 <= tick < self.n):
            raise ValueError(f"tick {tick} fora da grade [0,{self.n})")
        self.cnt[lado][tick] += 1
        self.sz[lado][tick] += q
        if lado == BID and tick > self.best[BID]:
            self.best[BID] = tick
        if lado == ASK and tick < self.best[ASK]:
            self.best[ASK] = tick

    def _reduz(self, lado, tick, dq, remove, tipo_redutor, exec_ticks):
        self.sz[lado][tick] -= dq
        if remove:
            self.cnt[lado][tick] -= 1
        assert self.sz[lado][tick] >= 0 and self.cnt[lado][tick] >= 0, \
            f"quantidade negativa no tick {tick} lado {lado}"
        if self.cnt[lado][tick] == 0:
            self.esvaz[lado][tick] = Esvaziamento(
                self.k, (lado, tick) in exec_ticks, tipo_redutor)
            if tick == self.best[lado]:
                self._rescan(lado)

    # ---------------------------------------------------------------- evento
    # Desvio aprovado D-1: um evento de casamento pode ocupar vários pacotes
    # (msg_seq_num) consecutivos com o mesmo sending_time. O chamador abre o
    # evento (iniciar), aplica pacote a pacote (pacote, que devolve o saldo
    # "volume negociado sem RLP − quantidade executada") e fecha (fechar).
    # Uma D/C é execução se o seu (lado, tick) foi negociado neste evento por
    # um print NÃO RLP e ainda há saldo a executar.
    def iniciar(self, seq: int, t: int, fase: str):
        self.k += 1
        self._ev = {"seq": seq, "t": t, "fase": fase, "precos": set(), "saldo": 0,
                    "volume": 0, "volume_nrl": 0, "neg": False, "neg_nrl": False,
                    "exec_ticks": set(), "n_exec": 0, "q_exec": 0, "anom": [], "n_pacotes": 0,
                    "pq": 0}

    def pacote(self, msgs: list) -> int:
        ev = self._ev
        ev["n_pacotes"] += 1
        exec_ticks = ev["exec_ticks"]
        for tipo, lado, tick, q, rl in msgs:
            if tipo == "T":
                ev["neg"] = True
                ev["volume"] += q
                ev["pq"] += tick * q
                if not rl:
                    ev["neg_nrl"] = True
                    ev["volume_nrl"] += q
                    ev["saldo"] += q
                    ev["precos"].add(tick)
        anom = ev["anom"]
        for tipo, lado, tick, q, oid in msgs:
            if tipo == "T":
                continue
            if tipo == "N":
                if oid in self.ordens:
                    self.n_anom["oid_duplicado"] += 1
                    anom.append("oid_duplicado")
                    lo, to, qo = self.ordens.pop(oid)
                    self._reduz(lo, to, qo, True, "cancel", exec_ticks)
                self.ordens[oid] = (lado, tick, q)
                self._add(lado, tick, q)
            elif tipo == "D":
                o = self.ordens.pop(oid, None)
                if o is None:
                    self.n_anom["delete_orfao"] += 1
                    anom.append("delete_orfao")
                    continue
                lo, to, qo = o
                e_exec = to in ev["precos"] and ev["saldo"] > 0
                if e_exec:
                    exec_ticks.add((lo, to))
                    ev["n_exec"] += 1
                    ev["q_exec"] += qo
                    ev["saldo"] -= qo
                self._reduz(lo, to, qo, True, "exec" if e_exec else "cancel", exec_ticks)
            elif tipo == "C":
                o = self.ordens.get(oid)
                if o is None:
                    self.n_anom["change_orfao"] += 1
                    anom.append("change_orfao")
                    continue
                lo, to, qo = o
                if tick != to:
                    # modificação de preço = cancelamento + inclusão (especificação)
                    self.ordens.pop(oid)
                    self._reduz(lo, to, qo, True, "cancel", exec_ticks)
                    self.ordens[oid] = (lo, tick, q)
                    self._add(lo, tick, q)
                    anom.append("change_com_preco")
                    continue
                if q > qo:
                    self.n_anom["change_aumenta"] += 1
                    anom.append("change_aumenta")
                    self.sz[lo][to] += q - qo
                    self.ordens[oid] = (lo, to, q)
                    continue
                e_exec = to in ev["precos"] and ev["saldo"] > 0
                if e_exec:
                    exec_ticks.add((lo, to))
                    ev["n_exec"] += 1
                    ev["q_exec"] += qo - q
                    ev["saldo"] -= qo - q
                else:
                    self.n_anom["change_sem_negocio"] += 1
                    anom.append("change_sem_negocio")
                self.ordens[oid] = (lo, to, q)
                self._reduz(lo, to, qo - q, False, "exec" if e_exec else "cancel", exec_ticks)
            else:
                raise ValueError(f"tipo de mensagem desconhecido: {tipo!r}")
        return ev["saldo"]

    def fechar(self) -> Estado:
        ev = self._ev
        b, a = self.best
        valido = b >= 0 and a < self.n and b < a
        lados_exec = {lt[0] for lt in ev["exec_ticks"]}
        # agressor: lado passivo consumido. Bid executado -> venda agressora.
        if lados_exec == {BID}:
            agr = -1
        elif lados_exec == {ASK}:
            agr = +1
        else:
            agr = 0
        e = Estado(
            k=self.k, seq=ev["seq"], t=ev["t"], fase=ev["fase"], b=b, a=a,
            qb=int(self.sz[BID][b]) if b >= 0 else 0,
            qa=int(self.sz[ASK][a]) if a < self.n else 0,
            valido=valido, teve_negocio=ev["neg"], agressor=agr,
            volume=ev["volume"], n_exec_msgs=ev["n_exec"], q_exec=ev["q_exec"], anomalias=ev["anom"])
        e.teve_negocio_nrl = ev["neg_nrl"]
        e.volume_nrl = ev["volume_nrl"]
        e.saldo_final = ev["saldo"]
        e.n_pacotes = ev["n_pacotes"]
        e.vwap_tick = ev["pq"] / ev["volume"] if ev["volume"] else float("nan")
        self._ev = None
        return e

    def aplicar(self, seq: int, t: int, fase: str, msgs: list) -> Estado:
        """Evento de um pacote só (testes sintéticos)."""
        self.iniciar(seq, t, fase)
        self.pacote(msgs)
        return self.fechar()

TICK_PONTOS = 5.0


def para_tick(preco_pontos: float, base_pontos: float) -> int:
    """Converte preço em pontos para tick inteiro; preço fora da grade interrompe."""
    q = (preco_pontos - base_pontos) / TICK_PONTOS
    r = round(q)
    if abs(q - r) > 1e-9:
        raise AssertionError(
            f"preço {preco_pontos} não é múltiplo do tick de {TICK_PONTOS} pontos: "
            "erro de reconstrução")
    return int(r)
