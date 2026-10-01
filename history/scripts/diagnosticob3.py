#!/usr/bin/env python3
"""
Diagnóstico da base UMDF da B3 (canal MBO) — percorre árvore de diretórios.

Descobre empiricamente domínios de enums, preenchimento de campos, integridade
das sequências e a distribuição do spread, sem assumir códigos a priori.

Uso:
    # Fase 1 — descoberta, sobre toda a árvore
    python diagnostico_b3_v2.py descobrir /share/fgv-quant/market-data/parquet/2025/09 \
        --security-id 200001274203

    # Fase 2 — topo de livro e spread, com resultado por pregão
    python diagnostico_b3_v2.py topo /share/fgv-quant/market-data/parquet/2025/09 \
        --security-id 200001274203 --bid 0 --offer 1 --new 0 --change 1 --delete 2

O caminho pode ser um arquivo .parquet, um diretório (varrido recursivamente)
ou um padrão glob. Filtros úteis: --dia 20250916, --max-arquivos 10.
"""

import argparse
import glob as globlib
import os
import re
import sys
from collections import Counter, defaultdict

import pyarrow.parquet as pq
import pyarrow.compute as pc
from concurrent.futures import ProcessPoolExecutor, as_completed, wait
from multiprocessing import Manager
import time

SAIDA = "diagnostico_saida"

COLS_TOPO = [
    "msg_type", "msg_seq_num", "sending_time", "transact_time", "trade_date",
    "symbol", "security_id", "security_group", "channel", "trading_session_sub_id",
    "security_trading_status", "rpt_seq", "market_depth",
]
CAMPOS_ENTRADA = [
    "md_entry_type", "md_update_action", "md_price_level", "md_entry_size",
    "order_id", "md_entry_position_no", "number_of_orders", "trade_id",
    "md_entry_buyer", "md_entry_seller", "trade_condition", "md_stream_id",
    "md_insert_time", "md_entry_time", "quote_condition", "symbol",
    "security_id", "rpt_seq", "price_band_type",
]
CATEGORICOS_ENTRADA = [
    "md_entry_type", "md_update_action", "md_price_level", "trade_condition",
    "md_stream_id", "quote_condition", "price_band_type",
]
CATEGORICOS_TOPO = [
    "msg_type", "trading_session_sub_id", "security_trading_status",
    "market_depth", "channel", "security_group",
]


# --------------------------------------------------------------------- utilidades

def decimal(v):
    if not isinstance(v, dict):
        return None
    e, m = v.get("exponent"), v.get("mantissa")
    return None if e is None or m is None else m * (10 ** e)


def para_ms(v):
    """Converte HHMMSSmmm (ex.: 132724257 = 13:27:24,257) em ms desde a meia-noite.

    Tratado como inteiro puro, esse formato cria saltos artificiais na virada de
    minuto (132759999 -> 132800000 salta 40.001 em vez de 1). Irrelevante numa
    janela de segundos; fatal na ponderação por tempo de um pregão inteiro.
    """
    if v is None:
        return None
    v = int(v)
    if v >= 10 ** 16:
        # AAAAMMDDHHMMSSmmm: as mensagens de estado de sessão trazem data
        # completa, ao contrário das entradas de livro, que trazem só o horário
        v = v % 10 ** 9
    elif v > 10 ** 12:                    # nanossegundos desde a meia-noite
        return v // 10 ** 6
    if not (10 ** 7 <= v < 24 * 10 ** 7):
        return v
    ms, seg = v % 1000, (v // 1000) % 100
    mi, h = (v // 100_000) % 100, v // 10_000_000
    if seg > 59 or mi > 59 or h > 23:
        return v
    return ((h * 60 + mi) * 60 + seg) * 1000 + ms


def interpretar_dias(spec):
    """Interpreta o argumento --dia.

    Aceita um dia (20250916), uma lista separada por vírgula
    (20250916,20250917) ou um intervalo fechado (20250915-20250930).
    Devolve None quando nada foi informado, o que significa processar tudo.
    """
    if not spec:
        return None
    dias = set()
    for parte in str(spec).split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "-" in parte:
            ini, fim = [x.strip() for x in parte.split("-", 1)]
            if not (ini.isdigit() and fim.isdigit()):
                sys.exit(f"intervalo inválido em --dia: {parte}")
            dias.add(("intervalo", ini, fim))
        else:
            if not parte.isdigit() or len(parte) != 8:
                sys.exit(f"valor inválido em --dia: {parte} (esperado AAAAMMDD)")
            dias.add(("exato", parte, parte))
    return dias


def dia_selecionado(d, filtro):
    if filtro is None:
        return True
    for tipo, ini, fim in filtro:
        if tipo == "exato" and d == ini:
            return True
        if tipo == "intervalo" and ini <= d <= fim:
            return True
    return False


def listar_arquivos(caminho, dia=None, maximo=None):
    """Aceita arquivo, diretório (recursivo) ou padrão glob."""
    if os.path.isdir(caminho):
        arqs = globlib.glob(os.path.join(caminho, "**", "*.parquet"), recursive=True)
    elif any(c in caminho for c in "*?["):
        arqs = globlib.glob(caminho, recursive=True)
    else:
        arqs = [caminho]
    arqs = [a for a in arqs if a.endswith(".parquet")]

    filtro = interpretar_dias(dia)
    if filtro is not None:
        # compara com a data extraída do nome, e não por substring em qualquer
        # posição: os números de sequência também contêm dígitos e produziriam
        # falsos positivos
        antes = len(arqs)
        arqs = [a for a in arqs if dia_selecionado(dia_do_arquivo(a), filtro)]
        if not arqs:
            disponiveis = sorted({dia_do_arquivo(a) for a in
                                  listar_arquivos(caminho)})
            print(f"nenhum arquivo casou com --dia {dia} (havia {antes} arquivos).")
            print("pregões disponíveis:")
            for d in disponiveis:
                print(f"    {d}")
            sys.exit(1)

    arqs.sort(key=chave_ordenacao)
    return arqs[:maximo] if maximo else arqs


def apenas_listar_dias(caminho):
    """Mostra os pregões disponíveis e o tamanho de cada um, sem processar nada."""
    arqs = listar_arquivos(caminho)
    if not arqs:
        sys.exit(f"nenhum .parquet encontrado em: {caminho}")
    por_dia = defaultdict(lambda: [0, 0])
    for a in arqs:
        d = dia_do_arquivo(a)
        por_dia[d][0] += 1
        try:
            por_dia[d][1] += os.path.getsize(a)
        except OSError:
            pass
    total_mb = sum(v[1] for v in por_dia.values()) / 1024 ** 2
    print(f"{len(arqs):,} arquivos em {len(por_dia)} pregões  ({total_mb:,.0f} MB no total)\n")
    print(f"    {'pregão':<12}{'arquivos':>10}{'tamanho (MB)':>16}")
    for d in sorted(por_dia):
        n, tam = por_dia[d]
        print(f"    {d:<12}{n:>10,}{tam/1024**2:>16,.0f}")
    print("\nProcesse um pregão por vez com --dia AAAAMMDD,")
    print("vários com --dia AAAAMMDD,AAAAMMDD ou um intervalo com --dia AAAAMMDD-AAAAMMDD.")


def chave_ordenacao(caminho):
    """Nomes seguem AAAAMMDDHHMMSS_seqinicial_seqfinal.parquet."""
    b = os.path.basename(caminho)
    m = re.match(r"(\d{8})(\d{6})?_(\d+)_(\d+)", b)
    if m:
        return (m.group(1), int(m.group(3)))
    return (b, 0)


def dia_do_arquivo(caminho):
    m = re.match(r"(\d{8})", os.path.basename(caminho))
    return m.group(1) if m else "sem-data"


def seqs_do_arquivo(caminho):
    m = re.match(r"\d{8}\d{0,6}_(\d+)_(\d+)", os.path.basename(caminho))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def cols_presentes(schema, desejadas):
    p = set(schema.names)
    return [c for c in desejadas if c in p]


def titulo(t):
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def tabela(cont, rotulo, limite=25, total=None):
    if not cont:
        print(f"  {rotulo}: nenhum valor observado")
        return
    tot = total if total is not None else sum(cont.values())
    print(f"  {rotulo}  ({len(cont)} valores distintos)")
    for v, n in cont.most_common(limite):
        print(f"      {str(v)[:34]:<34} {n:>13,}  {100.0*n/tot if tot else 0:6.2f}%")
    if len(cont) > limite:
        print(f"      ... e mais {len(cont) - limite} valores")


def salvar_csv(nome, cab, linhas):
    os.makedirs(SAIDA, exist_ok=True)
    caminho = os.path.join(SAIDA, nome)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(",".join(cab) + "\n")
        for l in linhas:
            f.write(",".join('"' + str(c).replace('"', "'") + '"' for c in l) + "\n")
    return caminho


# --------------------------------------------------------------------- fase 1

def _varrer(args):
    """Wrapper para execução em processo separado."""
    """Varre uma lista de arquivos e devolve acumuladores mescláveis.

    Todas as estatísticas da fase de descoberta são contagens, e contagem é
    associativa: o resultado de varrer os arquivos em blocos e somar é idêntico
    ao de varrê-los em sequência. Isso permite paralelizar por pregão.
    """
    arqs, symbol, sec_id, batch, silencioso = args
    acc = _varrer_arquivos(arqs, symbol, sec_id, batch, silencioso)
    acc["_bytes"] = tamanho(arqs)
    acc["_arquivos"] = len(arqs)
    return acc


def descobrir(caminho, symbol=None, sec_id=None, batch=200_000, dia=None,
              max_arquivos=None, jobs=1):
    arqs = listar_arquivos(caminho, dia, max_arquivos)
    if not arqs:
        sys.exit(f"nenhum .parquet encontrado em: {caminho}")

    dias = sorted({dia_do_arquivo(a) for a in arqs})
    print(f"arquivos encontrados: {len(arqs):,}   pregões: {len(dias)}"
          f"   ({dias[0]} a {dias[-1]})")

    total_bytes = tamanho(arqs)
    print(f"volume total: {total_bytes/1024**3:.2f} GB")
    jobs = max(1, jobs)

    if jobs > 1:
        por_pregao = defaultdict(list)
        for a in arqs:
            por_pregao[dia_do_arquivo(a)].append(a)
        # blocos menores que um pregão dão progresso mais fino sem afetar o
        # resultado, já que as contagens são associativas
        alvo_bloco = max(20, len(arqs) // (jobs * 6))
        tarefas = []
        for d in dias:
            lista = por_pregao[d]
            for i in range(0, len(lista), alvo_bloco):
                tarefas.append((lista[i:i + alvo_bloco], symbol, sec_id, batch, True))
        print(f"{jobs} processos em paralelo, {len(tarefas)} blocos de até "
              f"{alvo_bloco} arquivos (contagens são associativas e somam-se)")

        prog = Progresso(total_bytes, len(arqs))
        acc = None
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futuros = [ex.submit(_varrer, t) for t in tarefas]
            for fut in as_completed(futuros):
                r = fut.result()
                prog.avancar(r.pop("_bytes", 0), r.pop("_arquivos", 1))
                if acc is None:
                    acc = r
                else:
                    _mesclar(acc, r)
        prog.concluir()
    else:
        acc = _varrer_arquivos(arqs, symbol, sec_id, batch, False,
                               Progresso(total_bytes, len(arqs)))

    _relatorio(acc, arqs, dias, symbol, sec_id)


def _mesclar(a, b):
    """Soma os acumuladores de dois blocos."""
    for k in ("n_msgs", "n_entradas", "n_alvo", "n_neg"):
        a[k] += b[k]
    for k in ("cat_topo", "cat_entrada", "cruzado", "tipos_por_msgtype",
              "contraparte_tipo", "por_dia"):
        for chave, cont in b[k].items():
            a[k][chave].update(cont)
    a["buyers"].update(b["buyers"])
    a["sellers"].update(b["sellers"])
    a["instr_n"].update(b["instr_n"])
    for et, lst in b["precos"].items():
        espaco = 200_000 - len(a["precos"][et])
        if espaco > 0:
            a["precos"][et].extend(lst[:espaco])
    for sid, lst in b["instr_px"].items():
        espaco = 5_000 - len(a["instr_px"][sid])
        if espaco > 0:
            a["instr_px"][sid].extend(lst[:espaco])
    # dict.update sobrescreveria a chave (dia, canal) de blocos do mesmo pregão,
    # descartando a contagem dos demais. A combinação correta soma as mensagens e
    # toma os extremos; as lacunas são recalculadas no relatório a partir deles.
    for ch, v in b["seq_estado"].items():
        ant = a["seq_estado"].get(ch)
        if ant is None:
            a["seq_estado"][ch] = list(v)
        else:
            ant[0] = min(ant[0], v[0])
            ant[1] = max(ant[1], v[1])
            ant[2] += v[2]
            ant[4] += v[4]
    for nome, limite in (("status_eventos", 20_000), ("noticias", 20_000),
                         ("tick_rules", 5_000)):
        a[nome] += b[nome][:max(0, limite - len(a[nome]))]


def _varrer_arquivos(arqs, symbol, sec_id, batch, silencioso, prog=None):

    n_msgs = n_entradas = n_alvo = n_neg = 0
    cat_topo = defaultdict(Counter)
    cat_entrada = {c: Counter() for c in CATEGORICOS_ENTRADA}
    cruzado = defaultdict(Counter)
    tipos_por_msgtype = defaultdict(Counter)
    buyers, sellers = Counter(), Counter()
    contraparte_tipo = defaultdict(Counter)
    precos = defaultdict(list)
    instr_n, instr_px = Counter(), defaultdict(list)
    # Guardar todos os msg_seq_num consumiria dezenas de gigabytes em treze
    # pregões (são milhões de mensagens por sessão). Como as mensagens chegam em
    # ordem de sequência, basta acompanhar o último visto para contar lacunas e
    # duplicidades em memória constante.
    seq_estado = {}    # (dia, canal) -> [primeiro, ultimo, n, faltando, duplicados]
    por_dia = defaultdict(Counter)
    tick_rules, noticias = [], []
    status_eventos = []

    for k, arq in enumerate(arqs, 1):
        d_arq = dia_do_arquivo(arq)
        pf = pq.ParquetFile(arq)
        nomes = set(pf.schema_arrow.names)
        cols = cols_presentes(pf.schema_arrow, COLS_TOPO)
        for extra in ("md_entries", "related_sym", "news_id", "news_source",
                      "headline", "orig_time"):
            if extra in nomes:
                cols.append(extra)

        if prog is not None:
            prog.avancar(tamanho([arq]), 1, nota=f"pregão {d_arq}",
                         forcar=(k == len(arqs)))

        for lote in pf.iter_batches(batch_size=batch, columns=cols):
            d = lote.to_pydict()
            n = lote.num_rows
            vazio = [None] * n
            for c in CATEGORICOS_TOPO:
                if c in d:
                    for v in d[c]:
                        cat_topo[c][v] += 1

            # cabeçalho: percorrido linha a linha, mas são poucos campos
            for i in range(n):
                n_msgs += 1
                por_dia[d_arq]["mensagens"] += 1
                canal = d.get("channel", vazio)[i]
                msn = d.get("msg_seq_num", vazio)[i]
                if canal is not None and msn is not None:
                    ch = (d_arq, canal)
                    e_ = seq_estado.get(ch)
                    if e_ is None:
                        seq_estado[ch] = [msn, msn, 1, 0, 0]
                    else:
                        if msn > e_[1] + 1:
                            e_[3] += msn - e_[1] - 1
                        elif msn <= e_[1]:
                            e_[4] += 1
                        if msn > e_[1]:
                            e_[1] = msn
                        if msn < e_[0]:
                            e_[0] = msn
                        e_[2] += 1

                st = d.get("security_trading_status", vazio)[i]
                if st is not None and len(status_eventos) < 20_000:
                    status_eventos.append((d_arq, st,
                                           d.get("trading_session_sub_id", vazio)[i],
                                           d.get("transact_time", vazio)[i]
                                           or d.get("sending_time", vazio)[i],
                                           d.get("security_id", vazio)[i]))

                if ("news_id" in d and d["news_id"][i] is not None
                        and len(noticias) < 20_000):
                    noticias.append((d_arq, d["news_id"][i],
                                     d.get("news_source", vazio)[i],
                                     d.get("orig_time", vazio)[i],
                                     str(d.get("headline", vazio)[i])[:90]))

                for item in (d.get("related_sym", vazio)[i] or []):
                    if not isinstance(item, dict):
                        continue
                    mpi = decimal(item.get("min_price_increment"))
                    for tr in (item.get("tick_rules") or []):
                        if isinstance(tr, dict) and len(tick_rules) < 5_000:
                            tick_rules.append((item.get("symbol"),
                                               decimal(tr.get("start_tick_price_range")),
                                               decimal(tr.get("end_tick_price_range")),
                                               decimal(tr.get("tick_increment")), mpi))

            # entradas: extração vetorizada, sem construir um dicionário por entrada
            if "md_entries" not in lote.schema.names:
                continue
            dados, pais = extrair_entradas(lote, CAMPOS_ENTRADA)
            tipos = dados["md_entry_type"]
            if tipos is None:
                continue
            precos_e = dados["_preco"]
            sid_pai = d.get("security_id", vazio)
            mtypes = d.get("msg_type", vazio)

            for c in CATEGORICOS_ENTRADA:
                col = dados.get(c)
                if col is None:
                    continue
                for v in col:
                    if v is not None:
                        cat_entrada[c][v] += 1

            preenchidos = {c: dados.get(c) for c in CAMPOS_ENTRADA}
            for k in range(len(tipos)):
                n_entradas += 1
                por_dia[d_arq]["entradas"] += 1
                et = tipos[k]
                sid = preenchidos["security_id"][k] if preenchidos["security_id"] else None
                if sid is None:
                    sid = sid_pai[pais[k]]
                instr_n[sid] += 1

                if symbol is not None:
                    sy = preenchidos["symbol"][k] if preenchidos["symbol"] else None
                    ok = ((sy if sy is not None else d.get("symbol", vazio)[pais[k]]) == symbol)
                elif sec_id is not None:
                    ok = (str(sid) == str(sec_id))
                else:
                    ok = True

                alvo_cont = cruzado[et]
                alvo_cont["__total__"] += 1
                for c in CAMPOS_ENTRADA:
                    col = preenchidos[c]
                    if col is not None and col[k] is not None:
                        alvo_cont[c] += 1

                mt = mtypes[pais[k]]
                if mt is not None:
                    tipos_por_msgtype[mt][et] += 1

                px = precos_e[k]
                if px is not None:
                    if len(precos[et]) < 200_000:
                        precos[et].append(px)
                    oid_col = preenchidos["order_id"]
                    if oid_col is not None and oid_col[k] is not None and len(instr_px[sid]) < 5000:
                        instr_px[sid].append(px)

                if ok:
                    n_alvo += 1
                    por_dia[d_arq]["alvo"] += 1
                    tid = preenchidos["trade_id"]
                    if tid is not None and tid[k] is not None:
                        n_neg += 1
                        por_dia[d_arq]["negocios"] += 1
                    bcol, vcol = preenchidos["md_entry_buyer"], preenchidos["md_entry_seller"]
                    b = bcol[k] if bcol is not None else None
                    v = vcol[k] if vcol is not None else None
                    if b is not None:
                        buyers[b] += 1
                        contraparte_tipo[et]["buyer"] += 1
                    if v is not None:
                        sellers[v] += 1
                        contraparte_tipo[et]["seller"] += 1
                    contraparte_tipo[et]["__total__"] += 1


    return {
        "n_msgs": n_msgs, "n_entradas": n_entradas, "n_alvo": n_alvo, "n_neg": n_neg,
        "cat_topo": cat_topo, "cat_entrada": cat_entrada, "cruzado": cruzado,
        "tipos_por_msgtype": tipos_por_msgtype, "contraparte_tipo": contraparte_tipo,
        "buyers": buyers, "sellers": sellers, "precos": precos,
        "instr_n": instr_n, "instr_px": instr_px, "seq_estado": seq_estado,
        "por_dia": por_dia, "tick_rules": tick_rules, "noticias": noticias,
        "status_eventos": status_eventos,
    }


def _relatorio(acc, arqs, dias, symbol, sec_id):
    n_msgs = acc["n_msgs"]; n_entradas = acc["n_entradas"]
    n_alvo = acc["n_alvo"]; n_neg = acc["n_neg"]
    cat_topo = acc["cat_topo"]; cat_entrada = acc["cat_entrada"]
    cruzado = acc["cruzado"]; tipos_por_msgtype = acc["tipos_por_msgtype"]
    contraparte_tipo = acc["contraparte_tipo"]
    buyers = acc["buyers"]; sellers = acc["sellers"]; precos = acc["precos"]
    instr_n = acc["instr_n"]; instr_px = acc["instr_px"]
    seq_estado = acc["seq_estado"]; por_dia = acc["por_dia"]
    tick_rules = acc["tick_rules"]; noticias = acc["noticias"]
    status_eventos = acc["status_eventos"]

    # ------------------------------------------------------------------ relatório
    titulo("1. VISÃO GERAL DO ACERVO")
    print(f"  arquivos ............... {len(arqs):,}")
    print(f"  pregões ................ {len(dias)}  ({dias[0]} a {dias[-1]})")
    print(f"  mensagens .............. {n_msgs:,}")
    print(f"  entradas de mercado .... {n_entradas:,}")
    print(f"  alvo ................... {symbol or (f'security_id={sec_id}' if sec_id else 'todos')}")
    print(f"  entradas no alvo ....... {n_alvo:,}   negócios: {n_neg:,}")

    titulo("1b. VOLUME POR PREGÃO")
    print(f"      {'pregão':<12}{'mensagens':>14}{'entradas':>14}{'no alvo':>14}{'negócios':>12}")
    linhas_dia = []
    for dd in dias:
        c = por_dia[dd]
        print(f"      {dd:<12}{c['mensagens']:>14,}{c['entradas']:>14,}"
              f"{c['alvo']:>14,}{c['negocios']:>12,}")
        linhas_dia.append([dd, c["mensagens"], c["entradas"], c["alvo"], c["negocios"]])
    salvar_csv("volume_por_dia.csv",
               ["dia", "mensagens", "entradas", "entradas_alvo", "negocios"], linhas_dia)

    titulo("2. TIPOS DE MENSAGEM")
    tabela(cat_topo.get("msg_type", Counter()), "msg_type")
    print("\n  Entradas de livro por tipo de mensagem:")
    for mt, c in sorted(tipos_por_msgtype.items(), key=lambda x: -sum(x[1].values())):
        print(f"      msg_type={str(mt):<6} entradas={sum(c.values()):>13,}"
              f"   md_entry_type -> " + ", ".join(f"{k}:{v:,}" for k, v in c.most_common(8)))

    titulo("3. DOMÍNIOS OBSERVADOS")
    for c, cont in cat_topo.items():
        if c != "msg_type":
            tabela(cont, c)
    for c, cont in cat_entrada.items():
        tabela(cont, c, total=n_entradas)

    titulo("4. CLASSIFICAÇÃO DOS md_entry_type (por estrutura)")
    negocio, livro, estat = set(), set(), set()
    for et, c in cruzado.items():
        tot = c["__total__"] or 1
        if c["trade_id"] / tot > 0.5:
            negocio.add(et)
        elif (c["order_id"] + c["md_entry_position_no"]) / tot > 0.5:
            livro.add(et)
        else:
            estat.add(et)

    def passo(lst):
        """Menor incremento de preço observado.

        Os preços vêm de mantissa x 10^expoente e carregam ruído de ponto
        flutuante da ordem de 1e-11. Arredondar para duas casas ANTES de
        comparar evita que esse ruído seja lido como incremento de zero.
        """
        u = sorted({round(x, 2) for x in lst})[:4000]
        difs = [round(b - a, 2) for a, b in zip(u, u[1:])]
        difs = [d for d in difs if d > 0]
        return min(difs) if difs else None

    print(f"      {'tipo':<8}{'n':>13}{'mediana':>16}{'menor passo':>14}   classificação")
    linhas_tipo = []
    for et in sorted(cruzado, key=lambda x: -cruzado[x]["__total__"]):
        l = precos.get(et) or []
        med = sorted(l)[len(l) // 2] if l else None
        ps = passo(l) if len(l) > 5 else None
        cls = ("NEGOCIO" if et in negocio else
               "LADO DO LIVRO" if et in livro else "ESTATISTICA/DERIVADO")
        f = lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else "-"
        print(f"      {str(et):<8}{cruzado[et]['__total__']:>13,}{f(med):>16}{f(ps):>14}   {cls}")
        linhas_tipo.append([et, cruzado[et]["__total__"], med, ps, cls])
    salvar_csv("tipos_de_entrada.csv",
               ["md_entry_type", "n", "mediana", "menor_passo", "classificacao"], linhas_tipo)

    bid_s = [et for et in livro if cruzado[et]["md_entry_buyer"] > cruzado[et]["md_entry_seller"]]
    off_s = [et for et in livro if cruzado[et]["md_entry_seller"] > cruzado[et]["md_entry_buyer"]]
    if len(bid_s) == 1 and len(off_s) == 1:
        print(f"\n  >>> BID   -> md_entry_type={bid_s[0]}  (carrega md_entry_buyer)")
        print(f"  >>> OFFER -> md_entry_type={off_s[0]}  (carrega md_entry_seller)")
    if negocio:
        print(f"  >>> TRADE -> md_entry_type={sorted(negocio)}")
    if estat:
        print(f"  Não use no livro (agregados da sessão): {sorted(estat)}")

    titulo("5. PREENCHIMENTO POR TIPO DE ENTRADA")
    chaves = ["order_id", "md_entry_position_no", "number_of_orders", "md_price_level",
              "trade_id", "md_entry_buyer", "md_entry_seller", "md_insert_time"]
    print(f"      {'tipo':<8}{'total':>13}" + "".join(f"{c[:13]:>15}" for c in chaves))
    lp = []
    for et, c in sorted(cruzado.items(), key=lambda x: -x[1]["__total__"]):
        tot = c["__total__"] or 1
        print(f"      {str(et):<8}{c['__total__']:>13,}" +
              "".join(f"{100.0*c[k]/tot:>14.1f}%" for k in chaves))
        lp.append([et, c["__total__"]] + [f"{100.0*c[k]/tot:.2f}" for k in chaves])
    salvar_csv("preenchimento_por_tipo.csv", ["md_entry_type", "total"] + chaves, lp)

    titulo("6. CONTRAPARTES")
    if n_alvo == 0 and (symbol or sec_id):
        print("  ###  O filtro não casou com nenhuma entrada. security_id disponíveis:")
        for sid, ne in instr_n.most_common(10):
            print(f"       --security-id {sid}   ({ne:,} entradas)")
    print(f"      {'tipo':<8}{'total':>13}{'md_entry_buyer':>18}{'md_entry_seller':>18}")
    for et, c in sorted(contraparte_tipo.items(), key=lambda x: -x[1]["__total__"]):
        tot = c["__total__"] or 1
        print(f"      {str(et):<8}{c['__total__']:>13,}"
              f"{100.0*c['buyer']/tot:>17.1f}%{100.0*c['seller']/tot:>17.1f}%")
    print(f"\n  compradores distintos: {len(buyers):,}   vendedores distintos: {len(sellers):,}")
    tabela(buyers, "md_entry_buyer", limite=20)
    tabela(sellers, "md_entry_seller", limite=20)
    salvar_csv("contrapartes.csv", ["lado", "id", "n"],
               [["comprador", k, v] for k, v in buyers.most_common()] +
               [["vendedor", k, v] for k, v in sellers.most_common()])

    titulo("6b. INSTRUMENTOS")
    print(f"      {'security_id':<18}{'entradas':>14}{'preço mediano':>16}{'menor passo':>14}")
    li = []
    for sid, ne in instr_n.most_common(20):
        l = sorted(instr_px.get(sid) or [])
        med = l[len(l) // 2] if l else None
        ps = passo(l) if len(l) > 5 else None
        f = lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else "-"
        print(f"      {str(sid):<18}{ne:>14,}{f(med):>16}{f(ps):>14}")
        li.append([sid, ne, med, ps])
    salvar_csv("instrumentos.csv", ["security_id", "entradas", "mediana", "menor_passo"], li)

    titulo("7. REGRAS DE TIQUE")
    if tick_rules:
        for r in sorted(set(tick_rules))[:40]:
            print("      " + "  ".join(str(x) for x in r))
        salvar_csv("tick_rules.csv", ["symbol", "ini", "fim", "incremento", "min_price_inc"],
                   sorted(set(tick_rules)))
    else:
        print("  Nenhuma definição de instrumento nestes arquivos (vem em canal próprio).")

    titulo("8. INTEGRIDADE DAS SEQUÊNCIAS (por pregão)")
    print(f"      {'pregão':<12}{'canal':<10}{'mensagens':>14}{'faltando':>12}{'duplicados':>12}")
    lg = []
    for (dd, canal), (pri, ult, n_, _falt, dup) in sorted(seq_estado.items()):
        # recalculado a partir dos extremos: vale tanto para varredura serial
        # quanto para blocos paralelos, que chegam fora de ordem
        falt = max(0, (ult - pri + 1) - n_ + dup)
        alerta = "  <-- verificar" if falt else ""
        print(f"      {dd:<12}{str(canal):<10}{n_:>14,}{falt:>12,}{dup:>12,}{alerta}")
        lg.append([dd, canal, n_, pri, ult, falt, dup])
    salvar_csv("integridade.csv",
               ["dia", "canal", "n", "min", "max", "faltando", "duplicados"], lg)

    titulo("8b. FASES DA SESSÃO (mensagens de estado de negociação)")
    if status_eventos:
        print(f"  {len(status_eventos):,} eventos de mudança de estado.")
        print(f"      {'pregão':<10}{'status':>8}{'fase':>7}{'horário':>12}"
              f"{'security_id':>16}")
        alvo_str = str(sec_id) if sec_id else None
        for r in sorted(status_eventos, key=lambda x: (x[0], para_ms(x[3]) or 0))[:60]:
            if alvo_str and str(r[4]) != alvo_str:
                continue
            hh = para_ms(r[3])
            hstr = (f"{hh//3600000:02d}:{(hh//60000)%60:02d}:{(hh//1000)%60:02d}"
                    if isinstance(hh, int) and 0 <= hh < 86_400_000 else str(r[3]))
            fase = "-" if r[2] is None else str(r[2])
            print(f"      {r[0]:<10}{str(r[1]):>8}{fase:>7}{hstr:>12}{str(r[4]):>16}")
        salvar_csv("fases_sessao.csv",
                   ["dia", "security_trading_status", "trading_session_sub_id",
                    "horario", "security_id"], status_eventos)
        print("\n  Use estes horários para delimitar as janelas de leilão e de negociação")
        print("  contínua a excluir na fase topo (--hora-ini / --hora-fim).")
    else:
        print("  Nenhuma mensagem de estado de negociação neste recorte.")

    if noticias:
        titulo("9. NOTÍCIAS")
        print(f"  total: {len(noticias):,}")
        for r in noticias[:20]:
            print(f"      {r[0]}  [{r[2]}]  {r[4]}")
        salvar_csv("noticias.csv", ["dia", "news_id", "fonte", "orig_time", "headline"], noticias)

    titulo("ARQUIVOS GERADOS")
    for f in sorted(os.listdir(SAIDA)) if os.path.isdir(SAIDA) else []:
        print(f"  {os.path.join(SAIDA, f)}")


# --------------------------------------------------------------------- fase 2

def hhmmss_para_ms(v):
    """Converte HHMMSS ou HH:MM:SS em milissegundos desde a meia-noite."""
    if v is None:
        return None
    v = str(v).replace(":", "")
    if not v.isdigit() or len(v) not in (4, 6):
        sys.exit(f"horário inválido: {v} (use HHMMSS, ex.: 090000)")
    h, m = int(v[:2]), int(v[2:4])
    seg = int(v[4:6]) if len(v) == 6 else 0
    return ((h * 60 + m) * 60 + seg) * 1000


def tamanho(arqs):
    total = 0
    for a in arqs:
        try:
            total += os.path.getsize(a)
        except OSError:
            pass
    return total


def humano(seg):
    seg = int(seg)
    if seg < 60:
        return f"{seg}s"
    if seg < 3600:
        return f"{seg//60}m{seg%60:02d}s"
    return f"{seg//3600}h{(seg%3600)//60:02d}m"


class Progresso:
    """Barra de progresso por bytes lidos, com tempo decorrido e estimativa.

    Mede em bytes e não em número de arquivos porque o tamanho varia entre
    capturas: contar arquivos daria estimativa enviesada quando alguns cobrem
    períodos muito mais movimentados que outros.
    """

    def __init__(self, total_bytes, total_itens, rotulo="processando"):
        self.total = max(1, total_bytes)
        self.total_itens = max(1, total_itens)
        self.feito = 0
        self.itens = 0
        self.t0 = time.time()
        self.rotulo = rotulo
        self.ultima = 0.0

    def avancar(self, bytes_lidos, itens=1, nota="", forcar=False):
        self.feito += bytes_lidos
        self.itens += itens
        agora = time.time()
        # atualiza no máximo a cada dois segundos, para não poluir o console
        if not forcar and agora - self.ultima < 2.0 and self.itens < self.total_itens:
            return
        self.ultima = agora
        pct = 100.0 * self.feito / self.total
        decorrido = agora - self.t0
        resta = (decorrido / self.feito) * (self.total - self.feito) if self.feito else 0
        barra = "#" * int(pct // 5) + "." * (20 - int(pct // 5))
        print(f"  [{barra}] {pct:5.1f}%  {self.itens:,}/{self.total_itens:,} "
              f"arq  {self.feito/1024**3:.2f}/{self.total/1024**3:.2f} GB  "
              f"decorrido {humano(decorrido)}  resta ~{humano(resta)}"
              + (f"  {nota}" if nota else ""), flush=True)

    def concluir(self):
        print(f"  concluído em {humano(time.time() - self.t0)}"
              f"  ({self.feito/1024**3:.2f} GB, {self.itens:,} arquivos)", flush=True)


POT10 = {e: 10.0 ** e for e in range(-12, 13)}


def extrair_entradas(lote, campos):
    """Achata a coluna aninhada de entradas e devolve listas planas por campo.

    Converter o lote inteiro com to_pydict() constrói um dicionário Python por
    entrada, com todos os campos do struct, mesmo quando apenas cinco interessam.
    Achatar a lista uma vez e pedir campo a campo evita esse custo: em teste
    direto, a extração ficou cerca de quatro vezes mais rápida.
    """
    col = lote.column("md_entries")
    flat = pc.list_flatten(col)
    pais = pc.list_parent_indices(col).to_numpy(zero_copy_only=False)
    disponiveis = {flat.type.field(i).name for i in range(flat.type.num_fields)}

    out = {c: (flat.field(c).to_pylist() if c in disponiveis else None) for c in campos}

    if "md_entry_px" in disponiveis:
        px = flat.field("md_entry_px")
        mant = px.field("mantissa").to_pylist()
        expo = px.field("exponent").to_pylist()
        out["_preco"] = [None if (m is None or e is None) else m * POT10.get(e, 10.0 ** e)
                         for m, e in zip(mant, expo)]
    else:
        out["_preco"] = [None] * len(pais)
    return out, pais


def valores_equivalentes(v):
    """Conjunto com as formas textual e numérica de um código.

    md_entry_type chega como texto e md_update_action como inteiro; comparar
    contra as duas formas dispensa converter cada linha para string no laço.
    """
    if v is None:
        return set()
    formas = {v, str(v)}
    try:
        formas.add(int(v))
    except (TypeError, ValueError):
        pass
    return formas


class LivroPorOrdem:
    """Reconstrói o topo do livro mantendo todas as ofertas vivas.

    Em canal por ordem, a posição na fila é renumerada implicitamente: quando a
    oferta da frente sai, a segunda passa a ser a primeira sem que nenhuma
    mensagem informe isso. Acompanhar apenas as entradas com posição igual a 1
    faz o topo envelhecer e produz spreads absurdos. A alternativa correta é
    manter o conjunto de ofertas vivas e derivar o melhor preço de cada lado.
    """

    def __init__(self):
        self.ordens = {}                       # order_id -> (lado, preço, t_ins, qtd)
        self.orfas = 0                         # cancelamentos de ordens desconhecidas
        self.niveis = {"b": Counter(), "a": Counter()}
        self.vol_niveis = {"b": Counter(), "a": Counter()}
        self._melhor = {"b": None, "a": None}

    def _recalcular(self, lado):
        precos = [p for p, n in self.niveis[lado].items() if n > 0]
        if not precos:
            self._melhor[lado] = None
        else:
            self._melhor[lado] = max(precos) if lado == "b" else min(precos)

    def _remover(self, oid, contar_orfa=False):
        ant = self.ordens.pop(oid, None)
        if ant is None:
            # só é órfã quando um cancelamento cita uma ordem que o livro nunca
            # viu; a remoção que precede uma alteração não conta
            if contar_orfa:
                self.orfas += 1
            return
        lado, px = ant[0], ant[1]
        if len(ant) > 3 and ant[3]:
            self.vol_niveis[lado][px] -= ant[3]
            if self.vol_niveis[lado][px] <= 0:
                del self.vol_niveis[lado][px]
        self.niveis[lado][px] -= 1
        if self.niveis[lado][px] <= 0:
            del self.niveis[lado][px]
            if self._melhor[lado] == px:
                self._recalcular(lado)

    def _inserir(self, oid, lado, px, t_ins=None, qtd=None):
        self.ordens[oid] = (lado, px, t_ins, qtd)
        self.niveis[lado][px] += 1
        if qtd:
            self.vol_niveis[lado][px] += qtd
        m = self._melhor[lado]
        if m is None or (px > m if lado == "b" else px < m):
            self._melhor[lado] = px

    def aplicar(self, oid, lado, px, acao_del, acao_upd, acao, t_ins=None, qtd=None):
        if oid is None:
            return
        if acao in acao_del:
            self._remover(oid, contar_orfa=True)
        elif acao in acao_upd and px is not None and lado is not None:
            self._remover(oid)
            self._inserir(oid, lado, px, t_ins, qtd)

    def ordens_no_topo(self, lado):
        m = self._melhor[lado]
        return self.niveis[lado].get(m, 0) if m is not None else 0

    def volume_no_topo(self, lado):
        """Quantidade total ofertada no melhor preço, em contratos."""
        m = self._melhor[lado]
        return self.vol_niveis[lado].get(m, 0) if m is not None else 0

    @property
    def melhor_compra(self):
        return self._melhor["b"]

    @property
    def melhor_venda(self):
        return self._melhor["a"]

    def limpar(self):
        self.__init__()


def _processar_pregao(args):
    """Processa um trecho de pregão. Isolado para permitir execução em paralelo.

    O trecho vem em duas partes: os arquivos de aquecimento, lidos apenas para
    povoar o livro, e os de medição, cujos estados entram na estatística. Isso
    permite dividir um mesmo pregão entre processos, já que o livro não pode ser
    reconstruído a partir do meio sem antes recompor as ofertas em repouso.
    """
    (arqs_aq, arqs, sec_id, symbol, bid, offer, novo_c, alterar, deletar,
     batch, ms_ini, ms_fim, banda, excluir, fuso_ms) = args

    bid_v, offer_v = valores_equivalentes(bid), valores_equivalentes(offer)
    del_v = valores_equivalentes(deletar)
    upd_v = valores_equivalentes(novo_c) | valores_equivalentes(alterar)
    alvo = None if sec_id is None else {sec_id, str(sec_id)}
    try:
        alvo = alvo | {int(sec_id)} if alvo else None
    except (TypeError, ValueError):
        pass

    CAMPOS = ["md_entry_type", "md_update_action", "order_id",
              "md_entry_time", "md_insert_time", "security_id", "symbol"]

    livro = LivroPorOrdem()
    spread = Counter()
    ref = None                 # preço de referência corrente, para o filtro de banda
    descartadas = 0
    t_ant = None
    # cobertura: por que parte da janela não entra na soma de tempo
    dt_negativo = dt_longo = 0
    dt_perdido_ms = 0
    sem_um_lado = 0
    # sem intervalos negativos nem longos, a soma dos dt telescopa e equivale ao
    # intervalo entre o primeiro e o último evento: o histograma por hora mostra
    # em que faixas do pregão os eventos efetivamente ocorrem
    por_hora = Counter()
    ev_por_hora = Counter()
    primeiro_te = ultimo_te = None
    n_ev = 0
    ordens_max = 0
    quebras = []
    seq_fim = None
    arqs_set = set(arqs)
    orfas_medicao = 0
    cruzados = Counter()      # (compra, venda) -> tempo
    cruz_espessura = {}       # (compra, venda) -> menor nº de ordens visto em cada topo
    cruz_janela = {}          # (compra, venda) -> (primeiro, último) instante

    for arq in list(arqs_aq) + list(arqs):
        medindo = arq in arqs_set
        i0, f0 = seqs_do_arquivo(arq)
        if seq_fim is not None and i0 is not None and i0 != seq_fim + 1:
            quebras.append((os.path.basename(arq), seq_fim, i0))
            livro.limpar()
            t_ant = None
        if f0 is not None:
            seq_fim = f0

        pf = pq.ParquetFile(arq)
        nomes = set(pf.schema_arrow.names)
        if "md_entries" not in nomes:
            continue
        cols = ["md_entries"] + [c for c in ("security_id", "transact_time", "sending_time")
                                 if c in nomes]

        for lote in pf.iter_batches(batch_size=batch, columns=cols):
            if lote.num_rows == 0:
                continue
            dados, pais = extrair_entradas(lote, CAMPOS)
            tipos = dados["md_entry_type"]
            if tipos is None:
                continue
            acoes = dados["md_update_action"]
            oids = dados["order_id"]
            precos = dados["_preco"]
            t_ent = dados["md_entry_time"] or dados["md_insert_time"]
            sids = dados["security_id"]

            sid_pai = lote.column("security_id").to_pylist() if "security_id" in nomes else None
            t_pai = None
            for c in ("transact_time", "sending_time"):
                if c in nomes:
                    t_pai = lote.column(c).to_pylist()
                    break

            # variáveis locais: evita busca de atributo no laço quente
            aplicar = livro.aplicar
            for k in range(len(tipos)):
                et = tipos[k]
                if et in bid_v:
                    lado = "b"
                elif et in offer_v:
                    lado = "a"
                else:
                    continue

                if alvo is not None:
                    sid = sids[k] if sids is not None and sids[k] is not None else (
                        sid_pai[pais[k]] if sid_pai is not None else None)
                    if sid not in alvo:
                        continue

                te = t_ent[k] if t_ent is not None and t_ent[k] is not None else (
                    t_pai[pais[k]] if t_pai is not None else None)
                te = para_ms(te)
                if te is not None and fuso_ms:
                    # o feed marca em UTC; converte-se para o fuso local para que
                    # as janelas informadas correspondam ao horário de pregão
                    te = (te + fuso_ms) % 86_400_000
                if te is None:
                    continue
                if ms_ini is not None and te < ms_ini:
                    continue
                if ms_fim is not None and te > ms_fim:
                    continue
                if excluir and any(x0 <= te <= x1 for x0, x1 in excluir):
                    # janelas de leilão: o livro cruza legitimamente, pois não há
                    # casamento até a formação do preço. Os eventos continuam
                    # atualizando o livro, mas o estado não entra na estatística.
                    if medindo:
                        aplicar(oids[k] if oids is not None else None,
                                "b" if et == bid else "a", precos[k], del_v, upd_v,
                                acoes[k] if acoes is not None else None)
                        t_ant = None
                    continue

                if medindo:
                    b, a = livro.melhor_compra, livro.melhor_venda
                    if t_ant is not None and (b is None or a is None):
                        sem_um_lado += 1
                    if t_ant is not None and b is not None and a is not None:
                        dt = te - t_ant
                        if dt < 0:
                            dt_negativo += 1
                        elif dt >= 60_000:
                            dt_longo += 1
                            dt_perdido_ms += dt
                        if 0 < dt < 60_000:
                            spread[round(a - b, 2)] += dt
                            por_hora[te // 3_600_000] += dt
                            if a < b:
                                # além do tempo, guarda a espessura mínima de cada
                                # topo: um preço distante sustentado por uma única
                                # ordem indica oferta presa no livro, e não
                                # mercado efetivamente cruzado
                                ch = (round(b, 2), round(a, 2))
                                cruzados[ch] += dt
                                nb = livro.ordens_no_topo("b")
                                na = livro.ordens_no_topo("a")
                                ant = cruz_espessura.get(ch)
                                cruz_espessura[ch] = (nb, na) if ant is None else (
                                    min(ant[0], nb), min(ant[1], na))
                                jan = cruz_janela.get(ch)
                                cruz_janela[ch] = (te, te) if jan is None else (
                                    min(jan[0], te), max(jan[1], te))
                    t_ant = te
                    ev_por_hora[te // 3_600_000] += 1
                    if primeiro_te is None or te < primeiro_te:
                        primeiro_te = te
                    if ultimo_te is None or te > ultimo_te:
                        ultimo_te = te

                px_k = precos[k]
                acao_k = acoes[k] if acoes is not None else None

                # O filtro de banda vale SOMENTE para inclusões e alterações.
                # Descartar um cancelamento é sempre errado: remover uma oferta
                # nunca corrompe o livro, e ignorar a remoção deixa a oferta
                # presa para sempre — foi o que inflou o livro cruzado quando o
                # filtro se aplicava a todos os eventos.
                if banda and px_k is not None and acao_k not in del_v:
                    if ref is None:
                        ref = px_k
                    elif abs(px_k - ref) / ref > banda:
                        descartadas += 1
                        continue
                    else:
                        # média exponencial sobre as inclusões aceitas: elas se
                        # concentram junto ao topo, então acompanham o mercado
                        ref += 0.01 * (px_k - ref)
                antes = livro.orfas
                aplicar(oids[k] if oids is not None else None, lado,
                        px_k, del_v, upd_v, acao_k)
                if medindo:
                    n_ev += 1
                    orfas_medicao += livro.orfas - antes

            if len(livro.ordens) > ordens_max:
                ordens_max = len(livro.ordens)

    return (spread, n_ev, ordens_max, quebras, orfas_medicao,
            (cruzados, cruz_espessura, cruz_janela), descartadas,
            tamanho(arqs), len(arqs),
            (dt_negativo, dt_longo, dt_perdido_ms, sem_um_lado),
            por_hora, ev_por_hora, primeiro_te, ultimo_te)


def topo(caminho, symbol, sec_id, bid, offer, novo, alterar, deletar,
         batch=200_000, dia=None, max_arquivos=None, hora_ini=None, hora_fim=None,
         jobs=1, aquecimento=20, banda=None, excluir=None, fuso=0):
    arqs = listar_arquivos(caminho, dia, max_arquivos)
    if not arqs:
        sys.exit(f"nenhum .parquet encontrado em: {caminho}")

    ms_ini, ms_fim = hhmmss_para_ms(hora_ini), hhmmss_para_ms(hora_fim)
    fuso_ms = int(round(float(fuso) * 3_600_000))
    if fuso_ms:
        print(f"fuso aplicado às marcações: {fuso:+g} h "
              f"(o feed marca em UTC; os horários abaixo já são locais)")
    # Aceita HHMMSS-HHMMSS (vale para todos os pregões) e também
    # AAAAMMDD:HHMMSS-HHMMSS, quando o leilão ocorre em faixa distinta em um dia
    # específico e excluí-la de todos descartaria negociação contínua legítima.
    excl_por_dia = defaultdict(list)
    excl_geral = []
    for trecho in (excluir or "").split(",") if excluir else []:
        trecho = trecho.strip()
        if not trecho:
            continue
        dia_pref = None
        if ":" in trecho:
            dia_pref, trecho = trecho.split(":", 1)
        if "-" not in trecho:
            sys.exit(f"--excluir espera [AAAAMMDD:]HHMMSS-HHMMSS, recebeu: {trecho}")
        a_, b_ = trecho.split("-", 1)
        par = (hhmmss_para_ms(a_), hhmmss_para_ms(b_))
        if dia_pref:
            excl_por_dia[dia_pref.strip()].append(par)
        else:
            excl_geral.append(par)
    if excl_geral or excl_por_dia:
        print(f"janelas excluídas: {len(excl_geral)} geral(is)"
              f" + {sum(len(v) for v in excl_por_dia.values())} específica(s)")
    por_pregao = defaultdict(list)
    for a in arqs:
        por_pregao[dia_do_arquivo(a)].append(a)
    dias = sorted(por_pregao)

    print(f"arquivos: {len(arqs):,}   pregões: {len(dias)} ({dias[0]} a {dias[-1]})")
    if ms_ini or ms_fim:
        print(f"janela: {hora_ini or 'início'} a {hora_fim or 'fim'}")
    jobs = max(1, jobs)
    # quantos blocos por pregão: com poucos pregões e muitos processos, divide-se
    # o próprio dia; cada bloco recebe arquivos de aquecimento para recompor o livro
    blocos = max(1, jobs // len(dias)) if jobs > len(dias) else 1

    tarefas, rotulos = [], []
    for d in dias:
        arqs_d = por_pregao[d]
        if blocos == 1:
            tarefas.append(([], arqs_d, sec_id, symbol, bid, offer, novo, alterar,
                            deletar, batch, ms_ini, ms_fim, banda,
                            excl_geral + excl_por_dia.get(d, []) or None, fuso_ms))
            rotulos.append(d)
            continue
        tam = max(1, len(arqs_d) // blocos)
        for b in range(blocos):
            ini = b * tam
            fim_ = len(arqs_d) if b == blocos - 1 else (b + 1) * tam
            if ini >= len(arqs_d):
                break
            aq = arqs_d[max(0, ini - aquecimento):ini]
            tarefas.append((aq, arqs_d[ini:fim_], sec_id, symbol, bid, offer, novo,
                            alterar, deletar, batch, ms_ini, ms_fim, banda,
                            excl_geral + excl_por_dia.get(d, []) or None, fuso_ms))
            rotulos.append(d)

    if blocos > 1:
        print(f"cada pregão dividido em {blocos} blocos, com {aquecimento} arquivos de")
        print("aquecimento por bloco para recompor o livro antes de medir")
    if jobs > 1:
        print(f"{min(jobs, len(tarefas))} processos em paralelo")

    total_bytes = tamanho(arqs)
    print(f"volume total: {total_bytes/1024**3:.2f} GB   blocos: {len(tarefas)}")
    prog = Progresso(total_bytes, len(arqs))

    parciais = []
    if jobs > 1 and len(tarefas) > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tarefas))) as ex:
            futuros = {ex.submit(_processar_pregao, t): rot
                       for t, rot in zip(tarefas, rotulos)}
            for fut in as_completed(futuros):
                r = fut.result()
                prog.avancar(r[7], r[8], nota=f"pregão {futuros[fut]}", forcar=True)
                parciais.append((futuros[fut], r[:7] + (r[9], r[10], r[11], r[12], r[13])))
    else:
        for rot, t in zip(rotulos, tarefas):
            r = _processar_pregao(t)
            prog.avancar(r[7], r[8], nota=f"pregão {rot}", forcar=True)
            parciais.append((rot, r[:7] + (r[9], r[10], r[11], r[12], r[13])))
    prog.concluir()


    # consolida os blocos de volta em um resultado por pregão
    resultados = {}
    espessuras, janelas = {}, {}
    desc_tot = 0
    cob = [0, 0, 0, 0]
    hora_tempo, hora_ev = Counter(), Counter()
    prim_te = ult_te = None
    for (rot, (sp, n_ev, omax, quebras, orfas, (cruz, esp, jan), desc, c_,
               ph, peh, p_te, u_te)) in parciais:
        desc_tot += desc
        for i_ in range(4):
            cob[i_] += c_[i_]
        hora_tempo.update(ph)
        hora_ev.update(peh)
        if p_te is not None and (prim_te is None or p_te < prim_te):
            prim_te = p_te
        if u_te is not None and (ult_te is None or u_te > ult_te):
            ult_te = u_te
        if rot not in resultados:
            resultados[rot] = [Counter(), 0, 0, [], 0, Counter()]
        acc = resultados[rot]
        acc[0].update(sp)
        acc[1] += n_ev
        acc[2] = max(acc[2], omax)
        acc[3] += quebras
        acc[4] += orfas
        acc[5].update(cruz)
        for ch, v in esp.items():
            ant = espessuras.get(ch)
            espessuras[ch] = v if ant is None else (min(ant[0], v[0]), min(ant[1], v[1]))
        for ch, v in jan.items():
            ant = janelas.get(ch)
            janelas[ch] = v if ant is None else (min(ant[0], v[0]), max(ant[1], v[1]))

    titulo("TOPO DE LIVRO — RESULTADO POR PREGÃO")
    print(f"      {'pregão':<12}{'eventos':>14}{'horas':>9}{'1 tique':>10}"
          f"{'cruzado':>10}{'travado':>10}{'tique':>8}{'livro máx':>12}")
    linhas, tot_g, quebras_tot = [], Counter(), []
    orfas_tot = ev_tot = 0
    cruz_detalhe = Counter()
    for d in dias:
        sp, n_ev, omax, quebras, orfas, cruz = resultados[d]
        cruz_detalhe.update(cruz)
        orfas_tot += orfas
        ev_tot += n_ev
        quebras_tot += [(d,) + q for q in quebras]
        tot = sum(sp.values())
        if not tot:
            print(f"      {d:<12}{n_ev:>14,}   sem estado de livro completo")
            continue
        pos = {k: v for k, v in sp.items() if k > 0}
        cruz = 100.0 * sum(v for k, v in sp.items() if k < 0) / tot
        trav = 100.0 * sum(v for k, v in sp.items() if k == 0) / tot
        menor = min(pos) if pos else 0
        p1 = 100.0 * pos[menor] / sum(pos.values()) if pos else 0.0
        print(f"      {d:<12}{n_ev:>14,}{tot/3_600_000:>9.2f}{p1:>9.2f}%"
              f"{cruz:>9.2f}%{trav:>9.2f}%{menor:>8,.0f}{omax:>12,}")
        linhas.append([d, n_ev, f"{tot/1000:.1f}", f"{p1:.4f}", f"{cruz:.4f}",
                       f"{trav:.4f}", menor, omax])
        for k, v in sp.items():
            tot_g[k] += v
    salvar_csv("spread_por_dia.csv",
               ["dia", "eventos", "segundos", "pct_um_tique", "pct_cruzado",
                "pct_travado", "tique", "ordens_vivas_max"], linhas)

    if tot_g:
        titulo("DISTRIBUIÇÃO AGREGADA DO SPREAD")
        g = sum(tot_g.values())
        print(f"      {'spread':>14}{'tempo (s)':>16}{'% do tempo':>14}")
        lg = []
        for s_, v in sorted(tot_g.items())[:30]:
            print(f"      {s_:>14,.2f}{v/1000:>16,.0f}{100.0*v/g:>13.2f}%")
            lg.append([s_, f"{v/1000:.1f}", f"{100.0*v/g:.4f}"])
        salvar_csv("spread_agregado.csv", ["spread", "segundos", "pct_tempo"], lg)
        pos = {k: v for k, v in tot_g.items() if k > 0}
        if pos:
            menor = min(pos)
            print(f"\n  >>> Spread no tique mínimo ({menor:,.0f} pontos) em "
                  f"{100.0*pos[menor]/sum(pos.values()):.2f}% do tempo de livro normal,"
                  f" em {len(linhas)} pregão(ões).")
        if ev_tot:
            pct_orf = 100.0 * orfas_tot / ev_tot
            print(f"\n  Cancelamentos de ordens não vistas pelo livro: {orfas_tot:,}"
                  f" ({pct_orf:.3f}% dos eventos medidos).")
            if pct_orf > 1.0:
                print("  >>> Proporção alta. Se o pregão foi dividido em blocos, aumente")
                print("      --aquecimento; se não foi, investigue lacunas de sequência.")
            else:
                print("  Proporção baixa: o aquecimento recompôs o livro adequadamente.")

        cruz_g = 100.0 * sum(v for k, v in tot_g.items() if k < 0) / g
        if desc_tot:
            pct_d = 100.0 * desc_tot / ev_tot if ev_tot else 0.0
            print(f"\n  Inclusões descartadas pelo filtro de banda: {desc_tot:,}"
                  f" ({pct_d:.3f}% dos eventos medidos).")
            if pct_d > 1.0:
                print("  >>> Proporção alta: a banda está estreita demais e passou a")
                print("      descartar ofertas legítimas. Alargue-a ou desligue o filtro.")
        if cruz_detalhe:
            titulo("DIAGNÓSTICO DO LIVRO CRUZADO")
            print(f"  {cruz_g:.2f}% do tempo com melhor compra acima da melhor venda.")
            hms = lambda ms: (f"{ms//3600000:02d}:{(ms//60000)%60:02d}:{(ms//1000)%60:02d}"
                              if isinstance(ms, int) else "-")
            print(f"      {'compra':>13}{'venda':>13}{'diferença':>12}"
                  f"{'ord.C':>7}{'ord.V':>7}{'tempo(s)':>10}{'de':>10}{'até':>10}")
            for ch, v in cruz_detalhe.most_common(15):
                b_, a_ = ch
                nb, na = espessuras.get(ch, (0, 0))
                j0, j1 = janelas.get(ch, (None, None))
                print(f"      {b_:>13,.2f}{a_:>13,.2f}{b_-a_:>12,.2f}"
                      f"{nb:>7,}{na:>7,}{v/1000:>10,.0f}{hms(j0):>10}{hms(j1):>10}")
            salvar_csv("livro_cruzado.csv",
                       ["melhor_compra", "melhor_venda", "diferenca",
                        "min_ordens_compra", "min_ordens_venda", "milissegundos"],
                       [[k[0], k[1], round(k[0]-k[1], 2),
                         espessuras.get(k, (0, 0))[0], espessuras.get(k, (0, 0))[1], v]
                        for k, v in cruz_detalhe.most_common()])
            print("\n  Como ler: se um dos lados aparece com preço muito distante do")
            print("  mercado e apenas uma ou poucas ordens, trata-se de oferta presa no")
            print("  livro — inserida antes da janela e cuja retirada não foi observada —")
            print("  e não de mercado efetivamente cruzado. Nesse caso o remédio é")
            print("  aumentar --aquecimento ou descartar ofertas fora das bandas de preço.")

    titulo("COBERTURA TEMPORAL")
    janela_s = ((ms_fim or 86_400_000) - (ms_ini or 0)) / 1000
    medido_s = sum(tot_g.values()) / 1000 / max(1, len(linhas))
    print(f"  janela solicitada por pregão .......... {janela_s/3600:.2f} h")
    print(f"  tempo efetivamente medido por pregão .. {medido_s/3600:.2f} h"
          f"  ({100.0*medido_s/janela_s if janela_s else 0:.1f}%)")
    print(f"  intervalos com um lado do livro vazio . {cob[3]:,}")
    print(f"  intervalos com tempo negativo ......... {cob[0]:,}")
    print(f"  intervalos maiores que um minuto ...... {cob[1]:,}"
          f"  (descartados: {cob[2]/1000/3600:.2f} h no total)")
    hm = lambda ms: (f"{ms//3600000:02d}:{(ms//60000)%60:02d}:{(ms//1000)%60:02d}"
                     if ms is not None else "-")
    print(f"  primeiro evento observado ............. {hm(prim_te)}")
    print(f"  último evento observado ............... {hm(ult_te)}")
    if hora_tempo:
        print(f"\n      {'hora':>6}{'tempo medido (s)':>20}{'eventos':>16}")
        for h in sorted(set(hora_tempo) | set(hora_ev)):
            print(f"      {h:>4}h{hora_tempo.get(h, 0)/1000/max(1, len(linhas)):>20,.0f}"
                  f"{hora_ev.get(h, 0):>16,}")
        print("  (tempo por pregão; horas ausentes não tiveram eventos no alvo)")

    if janela_s and medido_s / janela_s < 0.9:
        print("\n  >>> A cobertura está bem abaixo da janela. Se os intervalos negativos")
        print("      forem numerosos, as marcações de tempo não estão monotônicas — o mais")
        print("      provável é que parte das entradas traga o horário de inserção original")
        print("      da oferta, e não o do evento, o que retrocede o relógio nos")
        print("      cancelamentos. Nesse caso a ponderação por tempo está subestimada.")

    if quebras_tot:
        titulo("DESCONTINUIDADES DE SEQUÊNCIA ENTRE ARQUIVOS")
        for d, arq, f0, i0 in quebras_tot[:30]:
            print(f"      {d}  {arq[:46]}  fim anterior={f0:,}  início={i0:,}")
        print(f"  total: {len(quebras_tot):,}  (o livro foi reiniciado em cada ponto)")


def _processar_qpc(args):
    (arqs_aq, arqs, sec_id, bid, offer, novo_c, alterar, deletar,
     batch, ms_ini, ms_fim, excluir, fuso_ms, intervalo_ms) = args

    bid_v, offer_v = valores_equivalentes(bid), valores_equivalentes(offer)
    del_v = valores_equivalentes(deletar)
    upd_v = valores_equivalentes(novo_c) | valores_equivalentes(alterar)
    trade_v = valores_equivalentes("2")
    alvo = None
    if sec_id is not None:
        alvo = {sec_id, str(sec_id)}
        try:
            alvo.add(int(sec_id))
        except (TypeError, ValueError):
            pass

    CAMPOS = ["md_entry_type", "md_update_action", "order_id", "md_entry_time",
              "md_insert_time", "security_id", "md_entry_size", "trade_id",
              "rpt_seq", "md_entry_buyer", "md_entry_seller"]

    livro = LivroPorOrdem()
    arqs_set = set(arqs)
    seq_fim = None
    t_ant = None
    dia_corrente = None

    # acumuladores por (dia, bin, lado): sem o pregão na chave, intervalos de
    # mesmo horário em dias distintos se somariam no mesmo balde

    incl = Counter()          # inclusões que se juntam à fila no melhor preço
    incl_undercut = Counter() # inclusões que estreitam o spread (undercutting real)
    incl_pos_exec = Counter() # melhorias que refazem o topo consumido por execução
    spread_antes = Counter()  # spread vigente no instante de cada melhoria
    canc = Counter()          # cancelamentos de ofertas no melhor preço
    execu = Counter()         # execuções de ofertas no melhor preço
    perm_soma = Counter()     # soma dos tempos de permanência no topo
    perm_n = Counter()
    corrida_soma = Counter()  # latência da corrida por prioridade
    corrida_n = Counter()
    perm_hist = Counter()     # distribuição da permanência, por faixa
    corr_hist = Counter()     # distribuição da latência, por faixa
    # Com relógio de milissegundo, a maior parte das corridas por prioridade se
    # resolve dentro de um único tique do relógio. A distância em número de
    # sequência do instrumento ordena esses eventos sem depender do tempo.
    corr_seq_soma = Counter()
    corr_seq_n = Counter()
    corr_seq_hist = Counter()
    # ------------------------------------------------------------------
    # Canal ativo de aquisição de prioridade.
    # Com o spread travado em um tique, não há como melhorar o preço postando
    # uma oferta: ela executaria de imediato. Resta consumir o topo do lado
    # oposto e, com o spread momentaneamente alargado, postar no nível que se
    # abriu. O preço se desloca por depleção de nível, não por estreitamento
    # de spread — e é por isso que o gráfico do spread não revela esse canal.
    desloc = Counter()        # (bin, lado, causa) -> deslocamentos do melhor preço
    agress_mesma = Counter()  # melhoria feita por quem acabou de agredir
    agress_outra = Counter()  # melhoria feita por terceiro
    # (lado da melhoria) -> (participante agressor, instante) após depleção
    pos_agressao = {"b": None, "a": None}
    JANELA_AGRESSAO = 1000    # ms de validade do vínculo entre agressão e postagem
    # ------------------------------------------------------------------
    # Natureza da ordem agressora.
    # As entradas de negócio não trazem order_id, de modo que a ordem que
    # agrediu não é identificável de forma direta. Contorna-se pelo rastro que
    # ela deixa: alterações de ofertas já existentes no livro. Distingue-se
    # alteração de preço — que faz a oferta perder prioridade de fila — de
    # alteração apenas de quantidade, e verifica-se se o novo preço cruza o
    # lado oposto, caso em que a alteração é ela própria a agressão.
    alt = Counter()           # (bin, lado, natureza) -> alterações
    alt_direcao = Counter()   # aproxima ou afasta do topo
    agressao_origem = Counter()  # ordem nova x alteração de oferta existente
    # participante -> instante da última alteração de preço, por lado
    alterou_recente = {"b": {}, "a": {}}
    JANELA_ALTERACAO = 1000
    alt_campos = Counter()    # o preço vem preenchido nas mensagens de alteração?
    # participação de cada corretora nas melhorias, para calcular a taxa de
    # coincidência esperada sob emparelhamento aleatório
    quota = Counter()
    prof_tempo = Counter()    # profundidade no topo ponderada pelo tempo
    # acumuladores por bin (comuns aos dois lados)
    spread_tempo = Counter()
    tempo_bin = Counter()
    volume = Counter()
    negocios = Counter()

    pendente = {"b": None, "a": None}   # (preço, instante) de um topo recém-formado
    # Um lado com dezenas de milhares de ofertas praticamente nunca fica vazio:
    # quando uma execução consome o melhor preço, o topo apenas recua para o
    # nível seguinte. A melhoria que vem em seguida repõe o preço consumido e não
    # é disputa espontânea de prioridade. Este marcador distingue os dois casos.
    topo_consumido = {"b": False, "a": False}

    for arq in list(arqs_aq) + list(arqs):
        medindo = arq in arqs_set
        dia_corrente = dia_do_arquivo(arq)
        i0, f0 = seqs_do_arquivo(arq)
        if seq_fim is not None and i0 is not None and i0 != seq_fim + 1:
            livro.limpar()
            t_ant = None
        if f0 is not None:
            seq_fim = f0

        pf = pq.ParquetFile(arq)
        nomes = set(pf.schema_arrow.names)
        if "md_entries" not in nomes:
            continue
        cols = ["md_entries"] + [c for c in ("security_id", "transact_time",
                                             "sending_time") if c in nomes]

        for lote in pf.iter_batches(batch_size=batch, columns=cols):
            if lote.num_rows == 0:
                continue
            dados, pais = extrair_entradas(lote, CAMPOS)
            tipos = dados["md_entry_type"]
            if tipos is None:
                continue
            acoes, oids = dados["md_update_action"], dados["order_id"]
            rseq = dados["rpt_seq"]
            compradores = dados["md_entry_buyer"]
            vendedores = dados["md_entry_seller"]
            precos, tam = dados["_preco"], dados["md_entry_size"]
            t_ent = dados["md_entry_time"] or dados["md_insert_time"]
            sids = dados["security_id"]
            sid_pai = lote.column("security_id").to_pylist() if "security_id" in nomes else None
            t_pai = None
            for c in ("transact_time", "sending_time"):
                if c in nomes:
                    t_pai = lote.column(c).to_pylist()
                    break

            # Preços negociados em cada mensagem. Uma retirada do livro é
            # atribuída a execução quando a mesma mensagem traz um negócio ao
            # mesmo preço; caso contrário, a cancelamento. Isso substitui a
            # regra temporal usada na literatura por identificação direta.
            precos_negociados = defaultdict(set)
            # as contrapartes de um negócio estão na entrada do negócio, não na
            # entrada de retirada da oferta: é preciso guardá-las por mensagem e
            # preço para depois identificar quem agrediu
            contrapartes = {}
            for k in range(len(tipos)):
                if tipos[k] in trade_v and precos[k] is not None:
                    pr = round(precos[k], 2)
                    precos_negociados[pais[k]].add(pr)
                    contrapartes[(pais[k], pr)] = (
                        compradores[k] if compradores else None,
                        vendedores[k] if vendedores else None)

            for k in range(len(tipos)):
                et = tipos[k]
                eh_negocio = et in trade_v
                if not eh_negocio and et not in bid_v and et not in offer_v:
                    continue

                if alvo is not None:
                    sid = sids[k] if sids is not None and sids[k] is not None else (
                        sid_pai[pais[k]] if sid_pai is not None else None)
                    if sid not in alvo:
                        continue

                te = t_ent[k] if t_ent is not None and t_ent[k] is not None else (
                    t_pai[pais[k]] if t_pai is not None else None)
                te = para_ms(te)
                if te is None:
                    continue
                if fuso_ms:
                    te = (te + fuso_ms) % 86_400_000
                if ms_ini is not None and te < ms_ini:
                    continue
                if ms_fim is not None and te > ms_fim:
                    continue
                if excluir and any(x0 <= te <= x1 for x0, x1 in excluir):
                    if medindo and not eh_negocio:
                        livro.aplicar(oids[k] if oids else None,
                                      "b" if et in bid_v else "a", precos[k],
                                      del_v, upd_v, acoes[k] if acoes else None, te)
                        t_ant = None
                    continue

                b_ini = (dia_corrente, te // intervalo_ms)

                if eh_negocio:
                    if medindo:
                        volume[b_ini] += tam[k] or 0
                        negocios[b_ini] += 1
                    continue

                faixa = lambda ms: ("0-1ms" if ms < 1 else "1-10ms" if ms < 10 else
                                    "10-100ms" if ms < 100 else "0,1-1s" if ms < 1000
                                    else "1-10s" if ms < 10_000 else
                                    "10-60s" if ms < 60_000 else ">60s")

                lado = "b" if et in bid_v else "a"
                px = precos[k]
                acao = acoes[k] if acoes is not None else None
                melhor = livro.melhor_compra if lado == "b" else livro.melhor_venda
                b0, a0 = livro.melhor_compra, livro.melhor_venda

                # ponderação por tempo do estado anterior
                if medindo and t_ant is not None and b0 is not None and a0 is not None:
                    dt = te - t_ant
                    if 0 < dt < 60_000 and a0 > b0:
                        bin_ant = (dia_corrente, t_ant // intervalo_ms)
                        spread_tempo[bin_ant] += (a0 - b0) * dt
                        tempo_bin[bin_ant] += dt
                        prof_tempo[(bin_ant, "b")] += livro.ordens_no_topo("b") * dt
                        prof_tempo[(bin_ant, "a")] += livro.ordens_no_topo("a") * dt
                if medindo:
                    t_ant = te

                # classificação das alterações antes de aplicar ao livro
                ant_ord = livro.ordens.get(oids[k]) if oids else None
                if medindo and acao in valores_equivalentes(alterar):
                    alt_campos["total"] += 1
                    alt_campos["com_preco" if px is not None else "sem_preco"] += 1
                    alt_campos["com_qtd" if tam[k] is not None else "sem_qtd"] += 1
                    if ant_ord is None:
                        alt_campos["oferta_desconhecida"] += 1
                if medindo and acao in valores_equivalentes(alterar) and ant_ord:
                    px_ant, qtd_ant = ant_ord[1], ant_ord[3]
                    qtd_nova = tam[k]
                    if px is not None and px != px_ant:
                        alt[(b_ini, lado, "preco")] += 1
                        # aproximar-se do topo é buscar execução; afastar-se é recuar
                        perto = (px > px_ant) if lado == "b" else (px < px_ant)
                        alt_direcao[(b_ini, lado,
                                     "aproxima" if perto else "afasta")] += 1
                        oposto_px = a0 if lado == "b" else b0
                        if oposto_px is not None and (
                                (px >= oposto_px) if lado == "b" else (px <= oposto_px)):
                            # o novo preço cruza o outro lado: a alteração é agressão
                            alt[(b_ini, lado, "cruza")] += 1
                        quem_alt = (compradores[k] if lado == "b" else vendedores[k]) \
                            if (compradores and vendedores) else None
                        if quem_alt is not None:
                            alterou_recente[lado][quem_alt] = te
                    elif qtd_nova is not None and qtd_ant is not None and qtd_nova != qtd_ant:
                        alt[(b_ini, lado, "quantidade")] += 1
                        alt_direcao[(b_ini, lado,
                                     "reduz" if qtd_nova < qtd_ant else "amplia")] += 1

                if acao in upd_v and px is not None:
                    if medindo:
                        if melhor is None or (px > melhor if lado == "b" else px < melhor):
                            if b0 is not None and a0 is not None and a0 > b0:
                                spread_antes[round(a0 - b0, 2)] += 1
                            desloc[(b_ini, lado, "melhoria")] += 1

                            # o participante da oferta é o comprador, se de
                            # compra, e o vendedor, se de venda
                            quem = (compradores[k] if lado == "b" else vendedores[k]) \
                                if (compradores and vendedores) else None
                            if quem is not None:
                                quota[quem] += 1
                            pend_ag = pos_agressao[lado]
                            if pend_ag is not None and te - pend_ag[1] <= JANELA_AGRESSAO:
                                if quem is not None and quem == pend_ag[0]:
                                    agress_mesma[(b_ini, lado)] += 1
                                else:
                                    agress_outra[(b_ini, lado)] += 1
                                pos_agressao[lado] = None

                            if topo_consumido[lado]:
                                incl_pos_exec[(b_ini, lado)] += 1
                            else:
                                incl_undercut[(b_ini, lado)] += 1
                            topo_consumido[lado] = False
                            pendente[lado] = (round(px, 2), te,
                                              rseq[k] if rseq else None)
                        elif px == melhor:
                            incl[(b_ini, lado)] += 1
                            pend = pendente[lado]
                            if pend is not None and pend[0] == round(px, 2):
                                lat = te - pend[1]
                                corrida_soma[(b_ini, lado)] += lat
                                corrida_n[(b_ini, lado)] += 1
                                corr_hist[faixa(lat)] += 1
                                r_k = rseq[k] if rseq else None
                                if r_k is not None and pend[2] is not None:
                                    d_seq = r_k - pend[2]
                                    if d_seq >= 0:
                                        corr_seq_soma[(b_ini, lado)] += d_seq
                                        corr_seq_n[(b_ini, lado)] += 1
                                        corr_seq_hist[
                                            "1" if d_seq <= 1 else "2-5" if d_seq <= 5
                                            else "6-20" if d_seq <= 20
                                            else "21-100" if d_seq <= 100 else ">100"] += 1
                                pendente[lado] = None
                elif acao in del_v and medindo:
                    ant = livro.ordens.get(oids[k] if oids else None)
                    if ant is not None and ant[1] == melhor:
                        foi_execucao = round(ant[1], 2) in precos_negociados.get(pais[k], ())
                        if foi_execucao:
                            execu[(b_ini, lado)] += 1
                        else:
                            canc[(b_ini, lado)] += 1
                        # se esta era a última oferta do melhor preço, registra
                        # como o topo foi desfeito, para classificar a melhoria seguinte
                        if livro.ordens_no_topo(lado) <= 1:
                            # o topo se esvazia: o melhor preço recua
                            topo_consumido[lado] = foi_execucao
                            desloc[(b_ini, lado,
                                    "execucao" if foi_execucao else "cancelamento")] += 1
                            if foi_execucao:
                                # quem agrediu é a contraparte ativa: ao consumir
                                # a venda, o agressor é o comprador, e vice-versa
                                cp = contrapartes.get((pais[k], round(ant[1], 2)))
                                agressor = (cp[0] if lado == "a" else cp[1]) if cp else None
                                oposto = "b" if lado == "a" else "a"
                                if agressor is not None:
                                    pos_agressao[oposto] = (agressor, te)
                                    # a agressão veio de ordem nova ou de oferta
                                    # já existente que teve o preço alterado?
                                    # o registro é consumido ao ser usado: uma
                                    # alteração dá origem a uma agressão, e não a
                                    # todas as que o participante fizer em seguida
                                    t_alt = alterou_recente[oposto].pop(agressor, None)
                                    agressao_origem[
                                        "alteracao" if (t_alt is not None and
                                                        te - t_alt <= JANELA_ALTERACAO)
                                        else "ordem_nova"] += 1
                        if ant[2] is not None:
                            dur = max(0, te - ant[2])
                            perm_soma[(b_ini, lado)] += dur
                            perm_n[(b_ini, lado)] += 1
                            perm_hist[faixa(dur)] += 1

                livro.aplicar(oids[k] if oids else None, lado, px,
                              del_v, upd_v, acao, te, tam[k])

    return (incl, incl_undercut, canc, execu, perm_soma, perm_n, corrida_soma,
            corrida_n, prof_tempo, spread_tempo, tempo_bin, volume, negocios,
            incl_pos_exec, perm_hist, corr_hist, spread_antes,
            corr_seq_soma, corr_seq_n, corr_seq_hist,
            desloc, agress_mesma, agress_outra, alt, alt_direcao, agressao_origem,
            alt_campos, quota, tamanho(arqs), len(arqs))


def qpc(caminho, sec_id, bid, offer, novo, alterar, deletar, batch=200_000,
        dia=None, max_arquivos=None, hora_ini=None, hora_fim=None, jobs=1,
        aquecimento=20, excluir=None, fuso=0, intervalo=60):
    arqs = listar_arquivos(caminho, dia, max_arquivos)
    if not arqs:
        sys.exit(f"nenhum .parquet encontrado em: {caminho}")

    ms_ini, ms_fim = hhmmss_para_ms(hora_ini), hhmmss_para_ms(hora_fim)
    fuso_ms = int(round(float(fuso) * 3_600_000))
    intervalo_ms = int(intervalo * 1000)

    excl_por_dia, excl_geral = defaultdict(list), []
    for trecho in (excluir or "").split(",") if excluir else []:
        trecho = trecho.strip()
        if not trecho:
            continue
        dia_pref = None
        if ":" in trecho:
            dia_pref, trecho = trecho.split(":", 1)
        a_, b_ = trecho.split("-", 1)
        par = (hhmmss_para_ms(a_), hhmmss_para_ms(b_))
        (excl_por_dia[dia_pref.strip()] if dia_pref else excl_geral).append(par)

    por_pregao = defaultdict(list)
    for a in arqs:
        por_pregao[dia_do_arquivo(a)].append(a)
    dias = sorted(por_pregao)

    print(f"arquivos: {len(arqs):,}   pregões: {len(dias)} ({dias[0]} a {dias[-1]})")
    print(f"intervalo de agregação: {intervalo}s")
    if fuso_ms:
        print(f"fuso aplicado: {fuso:+g} h")

    jobs = max(1, jobs)
    blocos = max(1, jobs // len(dias)) if jobs > len(dias) else 1
    tarefas, rotulos = [], []
    for d in dias:
        lista = por_pregao[d]
        excl_d = excl_geral + excl_por_dia.get(d, []) or None
        if blocos == 1:
            tarefas.append(([], lista, sec_id, bid, offer, novo, alterar, deletar,
                            batch, ms_ini, ms_fim, excl_d, fuso_ms, intervalo_ms))
            rotulos.append(d)
            continue
        tam = max(1, len(lista) // blocos)
        for b in range(blocos):
            ini = b * tam
            fim_ = len(lista) if b == blocos - 1 else (b + 1) * tam
            if ini >= len(lista):
                break
            tarefas.append((lista[max(0, ini - aquecimento):ini], lista[ini:fim_],
                            sec_id, bid, offer, novo, alterar, deletar, batch,
                            ms_ini, ms_fim, excl_d, fuso_ms, intervalo_ms))
            rotulos.append(d)

    total_bytes = tamanho(arqs)
    print(f"volume total: {total_bytes/1024**3:.2f} GB   blocos: {len(tarefas)}")
    prog = Progresso(total_bytes, len(arqs))

    acc = [Counter() for _ in range(28)]
    if jobs > 1 and len(tarefas) > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tarefas))) as ex:
            futuros = {ex.submit(_processar_qpc, t): rot
                       for t, rot in zip(tarefas, rotulos)}
            for fut in as_completed(futuros):
                r = fut.result()
                prog.avancar(r[28], r[29], nota=f"pregão {futuros[fut]}", forcar=True)
                for i in range(28):
                    acc[i].update(r[i])
    else:
        for rot, t in zip(rotulos, tarefas):
            r = _processar_qpc(t)
            prog.avancar(r[28], r[29], nota=f"pregão {rot}", forcar=True)
            for i in range(28):
                acc[i].update(r[i])
    prog.concluir()

    (incl, incl_undercut, canc, execu, perm_soma, perm_n, corrida_soma,
     corrida_n, prof_tempo, spread_tempo, tempo_bin, volume, negocios,
     incl_pos_exec, perm_hist, corr_hist, spread_antes,
     corr_seq_soma, corr_seq_n, corr_seq_hist,
     desloc, agress_mesma, agress_outra, alt, alt_direcao, agressao_origem,
     alt_campos, quota) = acc

    # ------------------------------------------------------------------ saída
    chaves = set(list(incl) + list(canc) + list(incl_undercut) + list(execu)
                 + list(incl_pos_exec))
    linhas = []
    for (b, lado) in sorted(chaves):
        d_, idx = b
        i_ = incl[(b, lado)]
        c_ = canc[(b, lado)]
        e_ = execu[(b, lado)]
        u_ = incl_undercut[(b, lado)]
        r_ = incl_pos_exec[(b, lado)]
        qpc_ = (i_ - c_) / (i_ + c_) if (i_ + c_) else None
        t_ = tempo_bin[b]
        ms = idx * intervalo_ms
        linhas.append([
            d_, f"{ms//3600000:02d}:{(ms//60000)%60:02d}:{(ms//1000)%60:02d}",
            "compra" if lado == "b" else "venda",
            i_, u_, r_, c_, e_,
            "" if qpc_ is None else f"{qpc_:.6f}",
            f"{perm_soma[(b, lado)]/perm_n[(b, lado)]:.1f}" if perm_n[(b, lado)] else "",
            f"{corrida_soma[(b, lado)]/corrida_n[(b, lado)]:.1f}" if corrida_n[(b, lado)] else "",
            f"{corr_seq_soma[(b, lado)]/corr_seq_n[(b, lado)]:.2f}" if corr_seq_n[(b, lado)] else "",
            desloc[(b, lado, "melhoria")],
            desloc[(b, lado, "execucao")],
            desloc[(b, lado, "cancelamento")],
            agress_mesma[(b, lado)],
            agress_outra[(b, lado)],
            alt[(b, lado, "preco")],
            alt[(b, lado, "quantidade")],
            alt[(b, lado, "cruza")],
            f"{prof_tempo[(b, lado)]/t_:.3f}" if t_ else "",
            f"{spread_tempo[b]/t_:.2f}" if t_ else "",
            volume[b], negocios[b], t_,
        ])

    caminho_csv = salvar_csv("qpc.csv",
                             ["dia", "hora", "lado", "inclusoes_no_topo",
                              "melhorias_espontaneas", "melhorias_pos_execucao",
                              "cancelamentos_no_topo", "execucoes_no_topo", "qpc",
                              "permanencia_media_ms", "latencia_corrida_ms",
                              "latencia_corrida_mensagens",
                              "desloc_por_melhoria", "desloc_por_execucao",
                              "desloc_por_cancelamento",
                              "melhoria_do_agressor", "melhoria_de_terceiro",
                              "alteracoes_de_preco", "alteracoes_de_quantidade",
                              "alteracoes_que_cruzam",
                              "ordens_no_topo_medio", "spread_medio", "volume",
                              "negocios", "tempo_ms"],
                             linhas)

    titulo("MEDIDAS DE COMPETIÇÃO POR PRIORIDADE DE FILA")
    print(f"  intervalos gerados: {len(linhas):,}   arquivo: {caminho_csv}")

    tot_i, tot_u = sum(incl.values()), sum(incl_undercut.values())
    tot_r, tot_c = sum(incl_pos_exec.values()), sum(canc.values())
    tot_e = sum(execu.values())
    tot_incl = tot_i + tot_u + tot_r
    print(f"\n  inclusões que entram na fila do melhor preço ... {tot_i:,}"
          f"  ({100.0*tot_i/tot_incl if tot_incl else 0:.2f}%)")
    print(f"  melhorias que repõem topo consumido por execução {tot_r:,}"
          f"  ({100.0*tot_r/tot_incl if tot_incl else 0:.2f}%)")
    print(f"  melhorias espontâneas (undercutting) .......... {tot_u:,}"
          f"  ({100.0*tot_u/tot_incl if tot_incl else 0:.2f}%)")
    print(f"  cancelamentos no melhor preço ................. {tot_c:,}")
    print(f"  execuções no melhor preço ..................... {tot_e:,}")
    if tot_i + tot_c:
        print(f"\n  QPC agregado .................................. "
              f"{(tot_i - tot_c)/(tot_i + tot_c):+.4f}")

    for nome, hist, soma, n in (("PERMANÊNCIA NO TOPO", perm_hist,
                                 sum(perm_soma.values()), sum(perm_n.values())),
                                ("LATÊNCIA DA CORRIDA POR PRIORIDADE", corr_hist,
                                 sum(corrida_soma.values()), sum(corrida_n.values()))):
        if not n:
            continue
        print(f"\n  {nome}  (média {soma/n:,.0f} ms, {n:,} observações)")
        ordem = ["0-1ms", "1-10ms", "10-100ms", "0,1-1s", "1-10s", "10-60s", ">60s"]
        tot_h = sum(hist.values()) or 1
        for f in ordem:
            if hist.get(f):
                barra = "#" * int(40 * hist[f] / tot_h)
                print(f"      {f:>9}  {hist[f]:>12,}  {100.0*hist[f]/tot_h:5.1f}%  {barra}")
        if hist.get("0-1ms", 0) / tot_h > 0.5:
            print("      >>> A maioria das observações cai abaixo de um milissegundo, que é")
            print("          a resolução do relógio do feed. A média não é informativa nessa")
            print("          faixa; para essa medida convém ordenar por número de sequência")
            print("          em vez de por tempo.")

    d_m = sum(v for (_, _, c), v in desloc.items() if c == "melhoria")
    d_e = sum(v for (_, _, c), v in desloc.items() if c == "execucao")
    d_c = sum(v for (_, _, c), v in desloc.items() if c == "cancelamento")
    d_t = d_m + d_e + d_c
    if d_t:
        print(f"\n  DESLOCAMENTOS DO MELHOR PREÇO  ({d_t:,} no total)")
        for rot, v in (("por melhoria de preço (canal passivo)", d_m),
                       ("por depleção via execução (canal ativo)", d_e),
                       ("por depleção via cancelamento", d_c)):
            barra = "#" * int(40 * v / d_t)
            print(f"      {rot:<40}{v:>13,}  {100.0*v/d_t:5.1f}%  {barra}")
        print("      O preço se move sobretudo por esvaziamento de nível, e não por")
        print("      estreitamento de spread: é esse o canal que a medida de undercutting")
        print("      de preço não enxerga em livro travado no tique mínimo.")

    a_p = sum(v for (_, _, t), v in alt.items() if t == "preco")
    a_q = sum(v for (_, _, t), v in alt.items() if t == "quantidade")
    a_x = sum(v for (_, _, t), v in alt.items() if t == "cruza")
    if alt_campos.get("total"):
        t_ = alt_campos["total"]
        print(f"\n  MENSAGENS DE ALTERAÇÃO: CAMPOS PREENCHIDOS  ({t_:,})")
        print(f"      com preço ....................... {alt_campos['com_preco']:>12,}"
              f"  ({100.0*alt_campos['com_preco']/t_:5.1f}%)")
        print(f"      com quantidade .................. {alt_campos['com_qtd']:>12,}"
              f"  ({100.0*alt_campos['com_qtd']/t_:5.1f}%)")
        print(f"      oferta ausente do livro ......... "
              f"{alt_campos['oferta_desconhecida']:>12,}"
              f"  ({100.0*alt_campos['oferta_desconhecida']/t_:5.1f}%)")
        if alt_campos.get("sem_preco", 0) / t_ > 0.5:
            print("      >>> O preço não é retransmitido na maioria das alterações. A")
            print("          ausência de alteração de preço pode ser artefato do protocolo,")
            print("          e não característica do mercado. Conclua com cautela.")
        else:
            print("      >>> O preço vem preenchido: a classificação entre alteração de")
            print("          preço e de quantidade é confiável.")

    if a_p + a_q:
        print(f"\n  NATUREZA DAS ALTERAÇÕES DE OFERTA  ({a_p + a_q:,} no total)")
        print(f"      alteração de preço (perde prioridade de fila) .. {a_p:>12,}"
              f"  ({100.0*a_p/(a_p+a_q):5.1f}%)")
        print(f"      alteração apenas de quantidade ................. {a_q:>12,}"
              f"  ({100.0*a_q/(a_p+a_q):5.1f}%)")
        ap = sum(v for (_, _, t), v in alt_direcao.items() if t == "aproxima")
        af = sum(v for (_, _, t), v in alt_direcao.items() if t == "afasta")
        if ap + af:
            print(f"      das de preço: {100.0*ap/(ap+af):.1f}% aproximam do topo, "
                  f"{100.0*af/(ap+af):.1f}% afastam")
        if a_p:
            print(f"      alterações cujo novo preço cruza o outro lado .. {a_x:>12,}"
                  f"  ({100.0*a_x/a_p:5.2f}% das de preço)")
            print("      Uma alteração que cruza é ela própria a agressão: a oferta deixa")
            print("      de esperar e passa a consumir liquidez do lado oposto.")

    tot_or = sum(agressao_origem.values())
    if tot_or:
        an = agressao_origem.get("ordem_nova", 0)
        aa = agressao_origem.get("alteracao", 0)
        print(f"\n  ORIGEM DA ORDEM AGRESSORA  ({tot_or:,} agressões que esvaziaram o topo)")
        print(f"      ordem nova enviada ao mercado ................. {an:>12,}"
              f"  ({100.0*an/tot_or:5.1f}%)")
        print(f"      oferta já no livro, com preço alterado ........ {aa:>12,}"
              f"  ({100.0*aa/tot_or:5.1f}%)")
        print("      A segunda categoria é economicamente distinta: o participante abre")
        print("      mão da prioridade que já detinha para consumir o outro lado.")

    tot_ag = sum(agress_mesma.values()) + sum(agress_outra.values())
    if tot_ag:
        m_ag = sum(agress_mesma.values())
        print(f"\n  AQUISIÇÃO ATIVA DE PRIORIDADE  ({tot_ag:,} sequências identificadas)")
        print(f"      melhoria feita por quem acabou de agredir ... {m_ag:>12,}"
              f"  ({100.0*m_ag/tot_ag:5.1f}%)")
        print(f"      melhoria feita por terceiro ................. "
              f"{tot_ag-m_ag:>12,}  ({100.0*(tot_ag-m_ag)/tot_ag:5.1f}%)")
        tq = sum(quota.values())
        if tq:
            esperado = sum((v / tq) ** 2 for v in quota.values())
            print(f"\n      Referência sob emparelhamento aleatório: {100*esperado:5.1f}%")
            print(f"      (soma dos quadrados das participações das {len(quota)} corretoras;")
            print(f"       com poucos participantes dominantes, coincidências ocorrem por")
            print(f"       acaso, e é contra esse patamar que a taxa deve ser lida)")
            if esperado > 0:
                print(f"      Razão entre observado e esperado: "
                      f"{(m_ag/tot_ag)/esperado:.1f} vezes")
        print("      Sequência: um participante consome o topo do lado oposto, o spread")
        print("      se alarga e ele posta no nível que se abriu. Quando o agressor e o")
        print("      ofertante coincidem, a agressão é o preço pago pela prioridade.")

    if sum(corr_seq_n.values()):
        print(f"\n  LATÊNCIA EM NÚMERO DE MENSAGENS  (média "
              f"{sum(corr_seq_soma.values())/sum(corr_seq_n.values()):,.2f} mensagens)")
        tot_cs = sum(corr_seq_hist.values()) or 1
        for f in ["1", "2-5", "6-20", "21-100", ">100"]:
            if corr_seq_hist.get(f):
                barra = "#" * int(40 * corr_seq_hist[f] / tot_cs)
                print(f"      {f:>7}  {corr_seq_hist[f]:>12,}  "
                      f"{100.0*corr_seq_hist[f]/tot_cs:5.1f}%  {barra}")
        print("      Distância em rpt_seq entre a formação de um novo topo e a chegada")
        print("      da oferta seguinte naquele preço. Independe da resolução do relógio.")

    if spread_antes:
        print(f"\n  SPREAD VIGENTE NO INSTANTE DE CADA MELHORIA")
        tot_sa = sum(spread_antes.values())
        for sp_, n_ in sorted(spread_antes.items())[:8]:
            barra = "#" * int(40 * n_ / tot_sa)
            print(f"      {sp_:>8,.0f} pontos  {n_:>12,}  {100.0*n_/tot_sa:5.1f}%  {barra}")
        um_tique = min(spread_antes) if spread_antes else 0
        print(f"      (nenhuma melhoria pode ocorrer com o spread no tique mínimo:")
        print(f"       sem nível de preço intermediário, não há o que ocupar)")

    print("\n  Note a distinção entre as três categorias de inclusão: apenas a terceira")
    print("  corresponde ao undercutting de preço da medida original, e ela só é possível")
    print("  quando o spread está acima de um tique. Refazer um lado consumido por uma")
    print("  execução não é disputa de prioridade e não deve ser confundido com ele.")


def tempos(caminho, sec_id=None, dia=None, max_arquivos=2, batch=100_000,
           amostra=200_000):
    """Inspeciona todos os campos de tempo e infere a precisão efetiva de cada um.

    O formato não pode ser suposto: o mesmo acervo mistura HHMMSSmmm nas entradas
    de livro e AAAAMMDDHHMMSSmmm nas mensagens de estado. E um campo declarado em
    nanossegundos pode, na prática, carregar apenas milissegundos preenchidos com
    zeros. O teste decisivo é olhar os dígitos finais: se terminarem sempre em
    zeros, a resolução anunciada não é a resolução real.
    """
    todos = listar_arquivos(caminho, dia)
    if not todos:
        sys.exit(f"nenhum .parquet encontrado em: {caminho}")
    # Os primeiros arquivos do dia contêm apenas mensagens de manutenção da
    # madrugada. A amostra é tirada do meio da lista, onde está a negociação.
    meio = len(todos) // 2
    arqs = todos[meio:meio + max(1, max_arquivos)]
    print(f"amostrando {len(arqs)} de {len(todos):,} arquivos, a partir do meio "
          f"do acervo: {os.path.basename(arqs[0])}")

    # Varredura ampla: qualquer campo cujo nome sugira tempo, data ou sequência.
    # Campos em texto merecem atenção especial — no padrão FIX eles trazem
    # AAAAMMDD-HH:MM:SS.nnnnnnnnn, e preservam precisão que a versão numérica
    # pode ter perdido na conversão para Parquet.
    PADRAO = re.compile(r"(time|date|timestamp|seq)", re.I)
    pf0 = pq.ParquetFile(todos[0])
    CAMPOS_MSG = [c for c in pf0.schema_arrow.names if PADRAO.search(c)]
    ent_tipo = None
    for campo in pf0.schema_arrow:
        if campo.name == "md_entries":
            ent_tipo = campo.type.value_type
    CAMPOS_ENT = ([f.name for f in ent_tipo if PADRAO.search(f.name)]
                  if ent_tipo is not None else [])
    print(f"campos candidatos: {len(CAMPOS_MSG)} no cabeçalho, "
          f"{len(CAMPOS_ENT)} nas entradas")

    amostras = defaultdict(list)
    pares_tempo = []          # (md_entry_time, md_insert_time) da MESMA entrada
    n = 0
    for arq in arqs:
        pf = pq.ParquetFile(arq)
        nomes = set(pf.schema_arrow.names)
        cols = [c for c in CAMPOS_MSG if c in nomes]
        if "md_entries" in nomes:
            cols.append("md_entries")
        if "security_id" in nomes:
            cols.append("security_id")
        for lote in pf.iter_batches(batch_size=batch, columns=cols):
            d = lote.to_pydict()
            for c in CAMPOS_MSG:
                if c in d:
                    amostras[c].extend(v for v in d[c][:5000] if v is not None)
            if "md_entries" in lote.schema.names:
                dados, _ = extrair_entradas(lote, CAMPOS_ENT)
                for c in CAMPOS_ENT:
                    col = dados.get(c)
                    if col:
                        amostras[c].extend(v for v in col[:20000] if v is not None)
                # o pareamento precisa ser por índice: colher os dois campos em
                # listas separadas e depois compará-las alinha registros distintos
                ce, ci = dados.get("md_entry_time"), dados.get("md_insert_time")
                if ce and ci and len(pares_tempo) < 100_000:
                    for x, y in zip(ce, ci):
                        if x is not None and y is not None:
                            pares_tempo.append((int(x), int(y)))
            n += lote.num_rows
            if n >= amostra:
                break
        if n >= amostra:
            break

    titulo("CAMPOS DE TEMPO — FORMATO E PRECISÃO EFETIVA")
    print(f"  amostra: {n:,} mensagens de {len(arqs)} arquivo(s)\n")
    print(f"      {'campo':<22}{'n':>10}{'dígitos':>9}{'distintos':>11}"
          f"{'term. 000':>11}{'term. 000000':>14}  exemplo")

    linhas, vazios = [], []
    for c in CAMPOS_MSG + CAMPOS_ENT:
        vals = amostras.get(c) or []
        if not vals:
            vazios.append(c)
            continue
        vals = vals[:50_000]
        numericos = [v for v in vals if isinstance(v, (int, float))]
        if numericos:
            dig = len(str(int(max(numericos))))
            z3 = sum(1 for v in numericos if int(v) % 1000 == 0)
            z6 = sum(1 for v in numericos if int(v) % 1_000_000 == 0)
            ex = str(int(numericos[0]))
            pz3 = 100.0 * z3 / len(numericos)
            pz6 = 100.0 * z6 / len(numericos)
        else:
            dig, pz3, pz6 = 0, 0.0, 0.0
            ex = str(vals[0])[:30]
        distintos = len(set(map(str, vals)))
        print(f"      {c:<22}{len(vals):>10,}{dig:>9}{distintos:>11,}"
              f"{pz3:>10.1f}%{pz6:>13.1f}%  {ex}")
        linhas.append([c, len(vals), dig, distintos, f"{pz3:.2f}", f"{pz6:.2f}", ex])
    salvar_csv("campos_de_tempo.csv",
               ["campo", "n", "digitos", "distintos", "pct_termina_000",
                "pct_termina_000000", "exemplo"], linhas)

    # resolução efetiva: quantos dígitos finais realmente variam
    if vazios:
        print(f"\n  CAMPOS PRESENTES NO SCHEMA MAS VAZIOS NA AMOSTRA ({len(vazios)})")
        print("      " + ", ".join(vazios))
        print("      Um campo declarado e não preenchido não guarda precisão alguma:")
        print("      a conversão para Parquet não o populou.")

    print("\n  DÍGITOS FINAIS QUE VARIAM  (resolução efetiva de cada campo)")
    for c in CAMPOS_MSG + CAMPOS_ENT:
        vals = [int(v) for v in (amostras.get(c) or [])[:50_000]
                if isinstance(v, (int, float))]
        if len(vals) < 50:
            continue
        zeros = 0
        for z in range(9):
            if all(v % (10 ** (z + 1)) == 0 for v in vals[:5000]):
                zeros = z + 1
            else:
                break
        distintos_ult = len({v % 1000 for v in vals})
        print(f"      {c:<22} termina em {zeros} zero(s) fixo(s); "
              f"{distintos_ult} valores distintos nos três últimos dígitos")

    print("\n  COMO LER")
    print("  9 dígitos  = HHMMSSmmm, resolução de milissegundo.")
    print("  15 dígitos = HHMMSSmmmuuunnn, resolução de nanossegundo.")
    print("  17 dígitos = AAAAMMDDHHMMSSmmm, data completa em milissegundo.")
    print("  Um campo com muitos dígitos mas que termina quase sempre em zeros")
    print("  carrega resolução menor do que o formato sugere: a precisão real é")
    print("  a do último dígito que efetivamente varia.")

    # comparação direta entre dois campos, quando ambos existirem
    if pares_tempo:
        iguais = sum(1 for x, y in pares_tempo if x == y)
        difs = [para_ms(x) - para_ms(y) for x, y in pares_tempo]
        difs = [d for d in difs if d is not None]
        pos = [d for d in difs if d > 0]
        print(f"\n  md_entry_time vs md_insert_time  ({len(pares_tempo):,} entradas "
              f"com ambos preenchidos)")
        print(f"      idênticos ......................... {100.0*iguais/len(pares_tempo):.1f}%")
        if pos:
            pos.sort()
            print(f"      entrada anterior ao evento ........ {100.0*len(pos)/len(difs):.1f}%")
            print(f"      idade da oferta: mediana {pos[len(pos)//2]:,} ms, "
                  f"p90 {pos[int(0.9*len(pos))]:,} ms, máx {pos[-1]:,} ms")
            print("\n      Divergência sistemática com md_insert_time anterior significa que")
            print("      este campo carrega o horário de entrada da oferta no livro, e não o")
            print("      do evento. Nesse caso o tempo de permanência na fila pode ser lido")
            print("      diretamente do registro de cancelamento, sem depender do")
            print("      rastreamento por order_id — e serve para validá-lo.")
        else:
            print("      Os dois campos marcam o mesmo instante; um deles é redundante.")


def serie(caminho, sec_id, bid, offer, novo, alterar, deletar, batch=200_000,
          dia=None, max_arquivos=None, hora_ini=None, hora_fim=None, fuso=0,
          saida="serie"):
    """Exporta a série de eventos: topo do livro e agressões que o consomem.

    Diferentemente do subcomando qpc, que agrega por intervalo, aqui se preserva
    o evento individual. São dois arquivos: a trajetória do melhor preço de cada
    lado, registrada a cada mudança, e a relação de agressões, com lado agressor,
    preço, volume e indicação de esgotamento do nível.

    A janela deve ser curta — a base produz milhões de eventos por pregão.
    """
    arqs = listar_arquivos(caminho, dia, max_arquivos)
    if not arqs:
        sys.exit(f"nenhum .parquet encontrado em: {caminho}")

    T = lambda x: None if x is None else str(x).strip()
    bid_v, offer_v = valores_equivalentes(bid), valores_equivalentes(offer)
    trade_v = valores_equivalentes("2")
    del_v = valores_equivalentes(deletar)
    upd_v = valores_equivalentes(novo) | valores_equivalentes(alterar)
    ms_ini, ms_fim = hhmmss_para_ms(hora_ini), hhmmss_para_ms(hora_fim)
    fuso_ms = int(round(float(fuso) * 3_600_000))
    alvo = None
    if sec_id is not None:
        alvo = {sec_id, str(sec_id)}
        try:
            alvo.add(int(sec_id))
        except (TypeError, ValueError):
            pass

    CAMPOS = ["md_entry_type", "md_update_action", "order_id", "md_entry_time",
              "md_insert_time", "security_id", "md_entry_size", "trade_id",
              "md_entry_buyer", "md_entry_seller"]

    livro = LivroPorOrdem()
    topo, agressoes = [], []
    b_ant = a_ant = None
    total_bytes = tamanho(arqs)
    prog = Progresso(total_bytes, len(arqs))

    for arq in arqs:
        pf = pq.ParquetFile(arq)
        nomes = set(pf.schema_arrow.names)
        if "md_entries" not in nomes:
            continue
        cols = ["md_entries"] + [c for c in ("security_id", "transact_time",
                                             "sending_time") if c in nomes]
        for lote in pf.iter_batches(batch_size=batch, columns=cols):
            if lote.num_rows == 0:
                continue
            dados, pais = extrair_entradas(lote, CAMPOS)
            tipos = dados["md_entry_type"]
            if tipos is None:
                continue
            acoes, oids = dados["md_update_action"], dados["order_id"]
            precos, tam = dados["_preco"], dados["md_entry_size"]
            t_ent = dados["md_entry_time"] or dados["md_insert_time"]
            sids = dados["security_id"]
            compradores, vendedores = dados["md_entry_buyer"], dados["md_entry_seller"]
            sid_pai = lote.column("security_id").to_pylist() if "security_id" in nomes else None
            t_pai = None
            for c in ("transact_time", "sending_time"):
                if c in nomes:
                    t_pai = lote.column(c).to_pylist()
                    break

            # contrapartes por mensagem e preço, para identificar quem agrediu
            contrapartes = {}
            for k in range(len(tipos)):
                if tipos[k] in trade_v and precos[k] is not None:
                    contrapartes[(pais[k], round(precos[k], 2))] = (
                        compradores[k] if compradores else None,
                        vendedores[k] if vendedores else None,
                        tam[k])

            for k in range(len(tipos)):
                et = tipos[k]
                if alvo is not None:
                    sid = sids[k] if sids is not None and sids[k] is not None else (
                        sid_pai[pais[k]] if sid_pai is not None else None)
                    if sid not in alvo:
                        continue
                te = t_ent[k] if t_ent is not None and t_ent[k] is not None else (
                    t_pai[pais[k]] if t_pai is not None else None)
                te = para_ms(te)
                if te is None:
                    continue
                if fuso_ms:
                    te = (te + fuso_ms) % 86_400_000
                if ms_ini is not None and te < ms_ini:
                    continue
                if ms_fim is not None and te > ms_fim:
                    continue

                if et in trade_v:
                    continue
                if et not in bid_v and et not in offer_v:
                    continue
                lado = "b" if et in bid_v else "a"
                acao = T(acoes[k]) if acoes is not None else None
                px = precos[k]

                if acao in del_v:
                    ant = livro.ordens.get(oids[k] if oids else None)
                    if ant is not None:
                        cp = contrapartes.get((pais[k], round(ant[1], 2)))
                        if cp is not None:
                            # o lado agressor é o oposto ao da oferta consumida
                            esgota = livro.ordens_no_topo(lado) <= 1 and ant[1] == (
                                livro.melhor_compra if lado == "b" else livro.melhor_venda)
                            agressoes.append((
                                te, "compra" if lado == "a" else "venda",
                                round(ant[1], 2), cp[2] or 0,
                                (cp[0] if lado == "a" else cp[1]) or "",
                                1 if esgota else 0))

                livro.aplicar(oids[k] if oids else None, lado, px,
                              del_v, upd_v, acao, te, tam[k])

                b, a = livro.melhor_compra, livro.melhor_venda
                if (b, a) != (b_ant, a_ant) and b is not None and a is not None:
                    topo.append((te, b, a))
                    b_ant, a_ant = b, a
        prog.avancar(tamanho([arq]), 1, forcar=True)
    prog.concluir()

    hms = lambda ms: (f"{ms//3600000:02d}:{(ms//60000)%60:02d}:"
                      f"{(ms//1000)%60:02d}.{ms%1000:03d}")
    c1 = salvar_csv(f"{saida}_topo.csv", ["hora", "ms", "melhor_compra", "melhor_venda"],
                    [[hms(t), t, b, a] for t, b, a in topo])
    c2 = salvar_csv(f"{saida}_agressoes.csv",
                    ["hora", "ms", "lado_agressor", "preco", "volume",
                     "participante", "esgotou_topo"],
                    [[hms(t), t, l, p, v, q, e] for t, l, p, v, q, e in agressoes])

    titulo("SÉRIE DE EVENTOS EXPORTADA")
    print(f"  mudanças no topo do livro ... {len(topo):,}   {c1}")
    print(f"  agressões .................. {len(agressoes):,}   {c2}")
    if agressoes:
        compra = sum(1 for a_ in agressoes if a_[1] == "compra")
        vol_c = sum(a_[3] for a_ in agressoes if a_[1] == "compra")
        vol_v = sum(a_[3] for a_ in agressoes if a_[1] == "venda")
        esg = sum(a_[5] for a_ in agressoes)
        print(f"  agressões de compra ........ {compra:,}  ({vol_c:,} contratos)")
        print(f"  agressões de venda ......... {len(agressoes)-compra:,}"
              f"  ({vol_v:,} contratos)")
        print(f"  que esgotaram o nível ...... {esg:,}"
              f"  ({100.0*esg/len(agressoes):.1f}%)")
    if topo:
        print(f"  janela coberta ............. {hms(topo[0][0])} a {hms(topo[-1][0])}")


def _hesitacao_pregao(args):
    """Processa um pregão. O horizonte de medição não cruza sessões, de modo que
    dividir por dia é exato e não aproximado."""
    from collections import deque
    (arqs, sec_id, bid, offer, novo_c, alterar, deletar, batch, ms_ini, ms_fim,
     fuso_ms, excl, horizontes, janela_rep, passo_controle, fila) = args

    T = lambda x: None if x is None else str(x).strip()
    bid_v, offer_v = valores_equivalentes(bid), valores_equivalentes(offer)
    trade_v = valores_equivalentes("2")
    del_v = valores_equivalentes(deletar)
    upd_v = valores_equivalentes(novo_c) | valores_equivalentes(alterar)
    alvo = None
    if sec_id is not None:
        alvo = {sec_id, str(sec_id)}
        try:
            alvo.add(int(sec_id))
        except (TypeError, ValueError):
            pass

    CAMPOS = ["md_entry_type", "md_update_action", "order_id", "md_entry_time",
              "md_insert_time", "security_id", "md_entry_size", "trade_id"]

    livro = LivroPorOrdem()
    pendentes = deque()
    registros = []
    n_msg = 0
    HMAX = max(horizontes)

    def resolver(te_atual, mid_atual):
        while pendentes and te_atual - pendentes[0]["te"] >= HMAX:
            p_ = pendentes.popleft()
            for h in horizontes:
                p_["mid"].setdefault(h, mid_atual)
            registros.append(p_)
        for p_ in pendentes:
            dt = te_atual - p_["te"]
            for h in horizontes:
                if dt >= h and h not in p_["mid"]:
                    p_["mid"][h] = mid_atual

    for arq in arqs:
        pf = pq.ParquetFile(arq)
        nomes = set(pf.schema_arrow.names)
        if "md_entries" not in nomes:
            continue
        cols = ["md_entries"] + [c for c in ("security_id", "transact_time",
                                             "sending_time") if c in nomes]
        for lote in pf.iter_batches(batch_size=batch, columns=cols):
            if lote.num_rows == 0:
                continue
            dados, pais = extrair_entradas(lote, CAMPOS)
            tipos = dados["md_entry_type"]
            if tipos is None:
                continue
            acoes, oids = dados["md_update_action"], dados["order_id"]
            precos, tam = dados["_preco"], dados["md_entry_size"]
            t_ent = dados["md_entry_time"] or dados["md_insert_time"]
            sids = dados["security_id"]
            sid_pai = lote.column("security_id").to_pylist() if "security_id" in nomes else None
            t_pai = None
            for c in ("transact_time", "sending_time"):
                if c in nomes:
                    t_pai = lote.column(c).to_pylist()
                    break

            neg = defaultdict(set)
            for k in range(len(tipos)):
                if tipos[k] in trade_v and precos[k] is not None:
                    neg[pais[k]].add(round(precos[k], 2))

            for k in range(len(tipos)):
                et = tipos[k]
                if et not in bid_v and et not in offer_v:
                    continue
                if alvo is not None:
                    sid = sids[k] if sids is not None and sids[k] is not None else (
                        sid_pai[pais[k]] if sid_pai is not None else None)
                    if sid not in alvo:
                        continue
                te = t_ent[k] if t_ent is not None and t_ent[k] is not None else (
                    t_pai[pais[k]] if t_pai is not None else None)
                te = para_ms(te)
                if te is None:
                    continue
                if fuso_ms:
                    te = (te + fuso_ms) % 86_400_000
                if ms_ini is not None and te < ms_ini:
                    continue
                if ms_fim is not None and te > ms_fim:
                    continue
                if excl and any(x0 <= te <= x1 for x0, x1 in excl):
                    continue

                n_msg += 1
                lado = "b" if et in bid_v else "a"
                acao = T(acoes[k]) if acoes is not None else None
                px = precos[k]
                b0, a0 = livro.melhor_compra, livro.melhor_venda
                mid = (b0 + a0) / 2 if (b0 is not None and a0 is not None
                                        and a0 > b0) else None
                if mid is not None:
                    resolver(te, mid)

                if acao in upd_v and px is not None:
                    alvo_px = round(px, 2)
                    for p_ in pendentes:
                        if p_["lado"] == lado and p_["preco"] == alvo_px:
                            if not p_["reposto"]:
                                p_["reposto"] = 1
                                p_["msgs"] = n_msg - p_["n0"]
                                p_["ms_ate"] = te - p_["te"]
                            if te - p_["te"] <= janela_rep:
                                p_["vol_reposto"] += tam[k] or 0

                if acao in del_v and mid is not None:
                    ant = livro.ordens.get(oids[k] if oids else None)
                    melhor = b0 if lado == "b" else a0
                    if (ant is not None and melhor is not None and ant[1] == melhor
                            and round(ant[1], 2) in neg.get(pais[k], ())):
                        esgota = livro.ordens_no_topo(lado) <= 1
                        # agressões que consomem o topo sem esgotá-lo servem de
                        # controle: têm a mesma natureza, sem o deslocamento
                        # mecânico do preço que a depleção provoca
                        pendentes.append({
                            "te": te, "n0": n_msg, "lado": lado,
                            "tipo": "deplecao" if esgota else "parcial",
                            "preco": round(ant[1], 2),
                            "sinal": 1 if lado == "a" else -1,
                            "vol": (livro.volume_no_topo(lado) if esgota
                                    else (tam[k] or 0)) or (tam[k] or 0),
                            "mid0": mid, "reposto": 0, "msgs": None,
                            "ms_ate": None, "vol_reposto": 0, "mid": {},
                        })
                elif (passo_controle and mid is not None
                      and n_msg % passo_controle == 0):
                    # controle temporal: instantes sem agressão alguma, com sinal
                    # alternado, para estimar a deriva de fundo do ponto médio
                    pendentes.append({
                        "te": te, "n0": n_msg, "lado": lado, "tipo": "controle",
                        "preco": None, "sinal": 1 if (n_msg // passo_controle) % 2
                        else -1, "vol": 0, "mid0": mid, "reposto": 0,
                        "msgs": None, "ms_ate": None, "vol_reposto": 0, "mid": {},
                    })

                livro.aplicar(oids[k] if oids else None, lado, px,
                              del_v, upd_v, acao, te, tam[k])
        if fila is not None:
            # o pai não tem como saber do andamento de um pregão longo se o
            # processo só reportar ao terminar: cada arquivo é anunciado
            fila.put((tamanho([arq]), 1))

    return registros, tamanho(arqs), len(arqs)


def hesitacao(caminho, sec_id, bid, offer, novo, alterar, deletar, batch=200_000,
              dia=None, max_arquivos=None, hora_ini=None, hora_fim=None, fuso=0,
              intervalo=60, jobs=1, excluir=None):
    """Mede a retração dos provedores de liquidez após a depleção de um nível.

    Para cada agressão que esgota o melhor preço registram-se duas famílias de
    grandeza. A hesitação: se o nível foi reocupado, em quantas mensagens e com
    que fração da profundidade consumida. E o alvo contra o qual validar: o
    impacto permanente de preço, isto é, quanto do deslocamento não se reverte.

    A comparação exige dois controles. As agressões que consomem o topo sem
    esgotá-lo têm a mesma natureza informacional, mas não deslocam o preço
    mecanicamente — são a referência correta para isolar o efeito da depleção.
    E instantes sem agressão alguma, com sinal alternado, estimam a deriva de
    fundo do ponto médio, que deve ser nula.
    """
    HORIZONTES = (1_000, 5_000, 30_000)
    JANELA_REPOSICAO = 5_000
    PASSO_CONTROLE = 5_000        # um instante de controle a cada N eventos

    arqs = listar_arquivos(caminho, dia, max_arquivos)
    if not arqs:
        sys.exit(f"nenhum .parquet encontrado em: {caminho}")

    ms_ini, ms_fim = hhmmss_para_ms(hora_ini), hhmmss_para_ms(hora_fim)
    fuso_ms = int(round(float(fuso) * 3_600_000))

    excl_geral, excl_dia = [], defaultdict(list)
    for trecho in (excluir or "").split(",") if excluir else []:
        trecho = trecho.strip()
        if not trecho:
            continue
        d_ = None
        if ":" in trecho:
            d_, trecho = trecho.split(":", 1)
        a_, b_ = trecho.split("-", 1)
        par = (hhmmss_para_ms(a_), hhmmss_para_ms(b_))
        (excl_dia[d_.strip()] if d_ else excl_geral).append(par)

    por_pregao = defaultdict(list)
    for a in arqs:
        por_pregao[dia_do_arquivo(a)].append(a)
    dias = sorted(por_pregao)
    print(f"arquivos: {len(arqs):,}   pregões: {len(dias)} ({dias[0]} a {dias[-1]})")

    jobs = max(1, min(jobs, len(dias)))
    gerente = Manager() if jobs > 1 else None
    fila = gerente.Queue() if gerente else None
    tarefas = [(por_pregao[d], sec_id, bid, offer, novo, alterar, deletar, batch,
                ms_ini, ms_fim, fuso_ms, excl_geral + excl_dia.get(d, []) or None,
                HORIZONTES, JANELA_REPOSICAO, PASSO_CONTROLE, fila) for d in dias]

    total_bytes = tamanho(arqs)
    print(f"volume: {total_bytes/1024**3:.2f} GB")
    prog = Progresso(total_bytes, len(arqs))
    registros = []
    if jobs > 1:
        print(f"{jobs} pregões em paralelo")
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futuros = {ex.submit(_hesitacao_pregao, t): d
                       for t, d in zip(tarefas, dias)}
            pendentes_f = set(futuros)
            concluidos = 0
            while pendentes_f:
                prontos, pendentes_f = wait(pendentes_f, timeout=2.0)
                while not fila.empty():
                    try:
                        by, na = fila.get_nowait()
                    except Exception:
                        break
                    prog.avancar(by, na, nota=f"{concluidos}/{len(dias)} pregões")
                for fut in prontos:
                    r, _, _ = fut.result()
                    for x in r:
                        x["dia"] = futuros[fut]
                    registros += r
                    concluidos += 1
                    print(f"  pregão concluído: {futuros[fut]}"
                          f"   ({concluidos}/{len(dias)})", flush=True)
    else:
        for d, t in zip(dias, tarefas):
            r, by, na = _hesitacao_pregao(t)
            for x in r:
                x["dia"] = d
            registros += r
            prog.avancar(by, na, nota=f"pregão {d}", forcar=True)
    prog.concluir()

    if not registros:
        sys.exit("nenhuma observação com horizonte completo")

    for r in registros:
        for h in HORIZONTES:
            m = r["mid"].get(h)
            r[f"imp{h}"] = None if m is None else r["sinal"] * (m - r["mid0"])
        r["frac"] = (min(1.0, r["vol_reposto"] / r["vol"])
                     if r["vol"] else None)
        # a completude da reposição é a variável de interesse: repor tudo indica
        # ausência de receio; não repor, ou repor pouco, indica o contrário
        r["classe"] = ("sem reposição" if not r["reposto"] else
                       "parcial" if (r["frac"] is not None and r["frac"] < 0.999)
                       else "integral")

    salvar_csv("hesitacao.csv",
               ["dia", "ms", "tipo", "lado", "preco", "volume", "sinal",
                "reposto", "classe", "msgs_ate_repor", "ms_ate_repor",
                "volume_reposto", "fracao_reposta"]
               + [f"impacto_{h}ms" for h in HORIZONTES],
               [[r["dia"], r["te"], r["tipo"],
                 "venda" if r["lado"] == "a" else "compra", r["preco"], r["vol"],
                 r["sinal"], r["reposto"], r["classe"], r["msgs"], r["ms_ate"],
                 r["vol_reposto"],
                 "" if r["frac"] is None else f"{r['frac']:.4f}"]
                + ["" if r[f"imp{h}"] is None else f"{r[f'imp{h}']:.2f}"
                   for h in HORIZONTES] for r in registros])

    def resumo(sub, h):
        v = [r[f"imp{h}"] for r in sub if r[f"imp{h}"] is not None]
        if len(v) < 2:
            return float("nan"), float("nan"), len(v)
        m = sum(v) / len(v)
        dp = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5
        return m, 1.96 * dp / len(v) ** 0.5, len(v)

    dep = [r for r in registros if r["tipo"] == "deplecao"]
    par = [r for r in registros if r["tipo"] == "parcial"]
    ctr = [r for r in registros if r["tipo"] == "controle"]

    titulo("IMPACTO PERMANENTE POR TIPO DE EVENTO")
    print("  A depleção desloca o melhor preço por construção. Sem comparação, o")
    print("  impacto observado não é interpretável: parte dele é mecânica.")
    print(f"\n      {'evento':<34}{'n':>10}" +
          "".join(f"{str(h//1000)+'s':>16}" for h in HORIZONTES))
    for rot, sub in (("depleção do nível", dep),
                     ("agressão sem esgotar o nível", par),
                     ("controle: instante sem agressão", ctr)):
        if not sub:
            continue
        cel = []
        for h in HORIZONTES:
            m, e, n = resumo(sub, h)
            cel.append(f"{m:+.2f}±{e:.2f}" if n > 1 else "-")
        print(f"      {rot:<34}{len(sub):>10,}" + "".join(f"{c:>16}" for c in cel))
    print("\n  O controle deve ficar em torno de zero; se não ficar, há deriva de")
    print("  fundo a descontar. A diferença entre depleção e agressão parcial é o")
    print("  efeito atribuível ao esgotamento do nível.")

    titulo("O INDICADOR: COMPLETUDE DA REPOSIÇÃO")
    print("  A demora em repor mostrou-se pouco informativa. A completude, não:")
    print("  repor integralmente indica ausência de receio; não repor, o contrário.")
    print(f"\n      {'classe':<20}{'n':>10}{'% ':>8}" +
          "".join(f"{str(h//1000)+'s':>16}" for h in HORIZONTES))
    linhas_v = []
    for classe in ("integral", "parcial", "sem reposição"):
        sub = [r for r in dep if r["classe"] == classe]
        if not sub:
            continue
        cel, vals = [], []
        for h in HORIZONTES:
            m, e, n = resumo(sub, h)
            cel.append(f"{m:+.2f}±{e:.2f}" if n > 1 else "-")
            vals.append(f"{m:.4f}")
        print(f"      {classe:<20}{len(sub):>10,}{100.0*len(sub)/len(dep):>7.1f}%"
              + "".join(f"{c:>16}" for c in cel))
        linhas_v.append([classe, len(sub)] + vals)
    salvar_csv("hesitacao_validacao.csv",
               ["classe", "n"] + [f"impacto_medio_{h}ms" for h in HORIZONTES],
               linhas_v)
    print("\n  O teste é a separação entre as classes: se os intervalos de confiança")
    print("  não se sobrepõem e a ordem é a esperada, a completude da reposição")
    print("  carrega informação sobre a seleção adversa realizada.")

    # ------------------------------------------------------------------
    # Controle por tamanho. A explicação concorrente mais óbvia é que níveis
    # não repostos sejam simplesmente os consumidos por ordens grandes, caso em
    # que o impacto refletiria tamanho, e não hesitação. Comparar as classes
    # dentro de faixas de volume semelhante separa as duas hipóteses.
    titulo("CONTROLE POR TAMANHO DA AGRESSÃO")
    vols = sorted(r["vol"] for r in dep if r["vol"])
    if len(vols) > 100:
        cortes = [vols[int(q * len(vols))] for q in (0.2, 0.4, 0.6, 0.8)]

        def faixa(v):
            for idx, c in enumerate(cortes):
                if v <= c:
                    return idx
            return len(cortes)

        rot_faixa = ([f"até {cortes[0]:,.0f}"]
                     + [f"{cortes[i]:,.0f}–{cortes[i+1]:,.0f}"
                        for i in range(len(cortes) - 1)]
                     + [f"acima de {cortes[-1]:,.0f}"])

        print("  Volume consumido no nível, em contratos. Cada linha compara as")
        print("  classes dentro de uma faixa de tamanho semelhante.")
        h_ref = HORIZONTES[min(1, len(HORIZONTES) - 1)]
        print(f"\n      {'faixa de volume':<22}" +
              "".join(f"{c:>22}" for c in ("integral", "parcial", "sem reposição")))
        linhas_c = []
        for f_ in range(len(cortes) + 1):
            sub_f = [r for r in dep if r["vol"] and faixa(r["vol"]) == f_]
            cel, vals = [], []
            for cl in ("integral", "parcial", "sem reposição"):
                sub = [r for r in sub_f if r["classe"] == cl]
                m, e, n = resumo(sub, h_ref)
                cel.append(f"{m:+.1f}±{e:.1f} (n={n:,})" if n > 1 else "-")
                vals += [f"{m:.3f}" if n > 1 else "", n]
            print(f"      {rot_faixa[f_]:<22}" + "".join(f"{c:>22}" for c in cel))
            linhas_c.append([rot_faixa[f_]] + vals)
        salvar_csv("hesitacao_por_volume.csv",
                   ["faixa_volume", "integral_media", "integral_n",
                    "parcial_media", "parcial_n", "sem_reposicao_media",
                    "sem_reposicao_n"], linhas_c)

        print(f"\n  (impacto em {h_ref//1000}s)")
        print("  Se a ordem entre classes se mantiver dentro de cada faixa, o efeito")
        print("  não decorre do tamanho da agressão. Se desaparecer nas faixas, o")
        print("  indicador está apenas captando ordens grandes.")

        print("\n  Volume mediano consumido, por classe:")
        for cl in ("integral", "parcial", "sem reposição"):
            v = sorted(r["vol"] for r in dep if r["classe"] == cl and r["vol"])
            if v:
                print(f"      {cl:<16}{v[len(v)//2]:>10,.0f} contratos"
                      f"   (média {sum(v)/len(v):,.1f})")


# --------------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pt = sub.add_parser("tempos")
    pt.add_argument("caminho")
    pt.add_argument("--dia", default=None)
    pt.add_argument("--max-arquivos", dest="max_arq", type=int, default=2)
    pt.add_argument("--security-id", dest="sec_id", default=None)
    for nome in ("descobrir", "topo", "qpc", "serie", "hesitacao"):
        p = sub.add_parser(nome)
        p.add_argument("caminho", help="arquivo, diretório (recursivo) ou glob")
        p.add_argument("--symbol", default=None)
        p.add_argument("--security-id", dest="sec_id", default=None)
        p.add_argument("--batch", type=int, default=200_000)
        p.add_argument("--dia", default=None,
                       help="pregão a processar: AAAAMMDD, lista separada por vírgula "
                            "ou intervalo AAAAMMDD-AAAAMMDD. Ausente, processa tudo.")
        p.add_argument("--listar-dias", dest="listar", action="store_true",
                       help="apenas lista os pregões disponíveis e encerra")
        p.add_argument("--max-arquivos", dest="max_arq", type=int, default=None)
        p.add_argument("--jobs", type=int, default=1,
                       help="processos em paralelo; em 'topo', havendo poucos "
                            "pregões, divide o próprio dia em blocos")
        if nome in ("topo", "qpc", "serie", "hesitacao"):
            p.add_argument("--bid", required=True)
            p.add_argument("--offer", required=True)
            p.add_argument("--new", dest="novo", required=True)
            p.add_argument("--change", dest="alterar", required=True)
            p.add_argument("--delete", dest="deletar", required=True)
            p.add_argument("--hora-ini", dest="hora_ini", default=None,
                           help="início da janela, HHMMSS (ex.: 100000)")
            p.add_argument("--hora-fim", dest="hora_fim", default=None,
                           help="fim da janela, HHMMSS (ex.: 175000)")
            if nome == "serie":
                p.add_argument("--saida", default="serie",
                               help="prefixo dos arquivos gerados")
            if nome in ("qpc", "hesitacao"):
                p.add_argument("--intervalo", type=int, default=60,
                               help="tamanho do intervalo de agregação, em segundos")
            p.add_argument("--fuso", type=float, default=0,
                           help="deslocamento aplicado às marcações do feed, em horas "
                                "(use -3 para converter UTC em horário de Brasília)")
            p.add_argument("--excluir", default=None,
                           help="janelas fora da estatística, separadas por vírgula: "
                                "HHMMSS-HHMMSS para todos os pregões ou "
                                "AAAAMMDD:HHMMSS-HHMMSS para um pregão específico")
            p.add_argument("--banda", type=float, default=None,
                           help="descarta ofertas afastadas mais que esta fração do "
                                "preço de referência (ex.: 0.05 = 5 por cento)")
            p.add_argument("--aquecimento", type=int, default=20,
                           help="arquivos lidos antes de cada bloco para recompor "
                                "o livro (padrão 20)")

    a = ap.parse_args()
    if getattr(a, "listar", False):
        apenas_listar_dias(a.caminho)
        return
    if a.cmd == "tempos":
        tempos(a.caminho, a.sec_id, a.dia, a.max_arq)
        return
    if a.cmd == "descobrir":
        descobrir(a.caminho, a.symbol, a.sec_id, a.batch, a.dia, a.max_arq, a.jobs)
    elif a.cmd == "hesitacao":
        hesitacao(a.caminho, a.sec_id, a.bid, a.offer, a.novo, a.alterar,
                  a.deletar, a.batch, a.dia, a.max_arq, a.hora_ini, a.hora_fim,
                  a.fuso, a.intervalo, a.jobs, a.excluir)
    elif a.cmd == "serie":
        serie(a.caminho, a.sec_id, a.bid, a.offer, a.novo, a.alterar, a.deletar,
              a.batch, a.dia, a.max_arq, a.hora_ini, a.hora_fim, a.fuso, a.saida)
    elif a.cmd == "qpc":
        qpc(a.caminho, a.sec_id, a.bid, a.offer, a.novo, a.alterar, a.deletar,
            a.batch, a.dia, a.max_arq, a.hora_ini, a.hora_fim, a.jobs,
            a.aquecimento, a.excluir, a.fuso, a.intervalo)
    else:
        topo(a.caminho, a.symbol, a.sec_id, a.bid, a.offer, a.novo, a.alterar,
             a.deletar, a.batch, a.dia, a.max_arq, a.hora_ini, a.hora_fim, a.jobs,
             a.aquecimento, a.banda, a.excluir, a.fuso)


if __name__ == "__main__":
    main()