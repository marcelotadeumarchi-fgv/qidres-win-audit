# run_log — Auditoria QIDres no WIN

Especificação: `auditoria_qidres_win.md` (fonte de verdade).
Sem git neste diretório: a versão do código é registrada por SHA256.

## Etapa 1 — testes sintéticos (2026-10-01)

Resultado: **12/12 passaram** (8 da especificação + 4 de integridade). Saída
completa em `saida_testes_sinteticos.txt`.

```
1ebd0e00…c4494  livro.py
bebbfb00…fb23   contagem.py
17bdd85c…b23    agregacao.py
fa8f7e4a…c44    regressao.py
1af56424…1cb    testes/test_sinteticos.py
```

Parâmetros: W ∈ {100, 1000, 5000} ms (padrão 1000); janelas R ∈ {10, 1} ms;
tick = 5 pontos.

## Interpretações em uso (PENDENTES de aprovação — não são desvios aprovados)

| ID | Ponto da especificação | Interpretação implementada |
|---|---|---|
| I0 | "mesmo número de sequência" | `msg_seq_num` (o `rpt_seq` é único por entrada e não agrupa) |
| I1 | "maior melhor bid observado em (t−W, t)" | estados válidos anteriores cuja vigência termina depois de t−W |
| I2 | "ask sofreu deterioração em (t−W, t)" | inclui a deterioração no próprio evento k |
| I3 | "primeira deterioração em até 10 ms após um negócio" | negócio do próprio evento conta (Δt=0); cada negócio habilita uma deterioração por lado |
| I4 | estado de referência após leilão | históricos de W e de 10 ms são zerados na referência |
| I5 | execução x cancelamento (versão A) | D ou C no mesmo `msg_seq_num` de um print, no preço do print = execução |
| I6 | BQID/AQID | definição R (do artigo); versão A gravada como diagnóstico adicional |
| I7 | reconstrução do book | motor por evento de casamento com as regras de `undercutting_valida.replay_dia`; validação mensagem a mensagem contra o replay antes do uso |

## Desvios aprovados

Nenhum até agora.

## Decisões do usuário

| Data | Ponto | Decisão |
|---|---|---|
| 2026-10-01 | Passo 6, comparação principal | QIDres 5 min desta auditoria × `qid_res` 5 min da tese (`tese_apendice_a.py`), como foi rodada (com `avg_spread_pts` mal medido) |
| 2026-10-01 | Passo 6, comparação secundária | Começar por `qid_signed` (H1 do protocolo pré-registrado); as demais (FQCR de H2/P2a etc.) depois. `qid_signed` não é uma QIDres; isso vai declarado no relatório |
| 2026-10-01 | Perguntas 2–7 da etapa 1 | "ok, vamos em frente": I0–I7 aprovadas como implementadas; teste 8 com DP teórico √(1−R²); motor novo validado contra `replay_dia` |
| 2026-10-01 | Pergunta 1 (amostra de 12 pregões, 1 trimestre) | EM ABERTO — bloqueia só o passo 5 |
| 2026-10-01 | Tabela de horários (`saida/tabela_horarios.csv`) | Aprovada ("sigo suas recomendações") |
| 2026-10-01 | Evento de casamento ≠ msg_seq_num (6,4% dos pacotes com negócio fragmentados, 30/09) | **Desvio aprovado D-1:** evento = pacote + pacotes seguintes com o mesmo `sending_time` enquanto o balanço (volume negociado sem RLP − quantidade executada no book) estiver aberto |
| 2026-10-01 | Prints RLP (`RL`, `L RL`; ~25% do volume, sem ordem visível no book) | **Desvio aprovado D-2:** versão R principal conta todo negócio (literal); variante R sem RLP adicionada; na versão A, print RLP nunca gera execução |

## Etapa 2 — um pregão (2025-09-30)

- Validação do motor contra `undercutting_valida.replay_dia`: 0 divergências de melhor bid/ask em 17.784.015 eventos (com D-1).
- Correções feitas durante a etapa (testes: 16/16):
  - C1: spread relativo e qvol estavam em índice de tick; agora em pontos (teste `unidades_spread`).
  - C2: I2 não via a deterioração do lado oposto no MESMO evento (ordem de processamento); corrigido (teste 11).
- D1: resíduo 0 no dia e nos 106 blocos de 5 min.
- Variante candidata D-1' (NÃO aprovada): evento atravessa a troca de ms enquanto o saldo > 0, teto de 100 ms. Saídas em `saida/passo2_D1b/`.
- Blocos de 5 min: grade alinhada ao relógio, só blocos inteiros dentro da janela válida (interpretação I8, pendente).
| 2026-10-01 | D-1' | **Aprovada** ("1. OK"): substitui D-1. Evento continua enquanto saldo > 0, mesmo trocando o ms, teto 100 ms (`TETO_CONTINUACAO_MS`) |
| 2026-10-01 | Inspeção manual da amostra de 30/09 | Aprovada ("2. OK") |
| 2026-10-01 | I8 (grade de 5 min alinhada ao relógio, só blocos inteiros) | Aprovada |
| 2026-10-01 | Pergunta 1 (amostra) | Usuário: "Vamos tentar QIDres por hora" — desenho a definir |
| 2026-10-01 | Pergunta 1 → **Desvio aprovado D-3** | Unidade = hora; parâmetros e S(QID) da **semana-calendário anterior** (no lugar do trimestre anterior); 1ª semana (15–19/09) só estima; efeitos fixos de horário no passo 1 |
| 2026-10-01 | Limiares D2–D11 | **Fixados como propostos** antes de rodar: D2 R²>0,80; D3 undercut<10% (W=1s); D4 frac_1tick médio>0,95; D5 ambos ≥0,95; D6 |média QID^R|<0,10 ou >10% negativos; D7 <90%; D8 R²<0,05 ou troca de sinal de b; D9 DP>2,0 ou |AR(1)|>0,3; D10 |ρ|>0,3; D11 ρ(QIDres^R,QIDres^U)<0,5 |

## Etapas 3–5 (2026-10-01, concluídas 15:52)

- Passo 3: 12 pregões, 1.269 blocos de 5 min, 3.440.287 eventos classificados. **D1 = 0 em todos os 12 dias e 1.269 blocos.** WINV25 (200001274203) é o de maior volume em todos os dias.
- Incidentes de execução: 2 quedas por memória. Causa isolada: `volume_por_instrumento` expandia o struct inteiro (148 GB); corrigido com projeção + streaming (4 GB). Também: estados em colunas compactas e D1 vetorizada (resultado idêntico ao anterior, verificado em 15/09: dia + 106/106 blocos). Testes: 17/17.
- Passo 4 e 5: ver `saida/audit_diagnostics.csv`, `saida/first_stage.csv`, `saida/qidres_hourly.parquet`, `saida/qid_hourly.parquet`.
- Painel horário: 120 horas; QIDres a partir de 22/09 (70 horas). Observações: 72,5% das horas têm 0 undercuts (QID^U = −1); S(QID) por semana entre 0,004 e 0,021.
- Interpretações I9 (horas: grade a partir do início da grade de 5 min, exclusões removidas) e I10 (Rolling = 45 horas anteriores): PENDENTES de aprovação.
| 2026-10-01 | Células com U (D9, D10, D11) | Aprovado ("1. OK"): marcadas "fracamente identificada" (72,5% das horas com 0 undercut; S(QID^U) 0,004–0,007) |
| 2026-10-01 | I9, I10 | Aprovadas ("2. OK") |
| 2026-10-01 | Passo 6, alinhamento | "(a) lets start in 5-minute as the thesis": comparação principal em **5 min** (versão intradiária da especificação); comparação horária como secundária |
| 2026-10-01 | Anomalias (saldo negativo, conflito de causa) | Ciente ("4. ok"); reportadas, sem correção |

## Etapa 6 e relatório (2026-10-01)

- QIDres intradiária de 5 min (7 pregões, controle = 5 pregões antes): `intra_spec` (como especificação, usa o próprio dia — conflito com a regra 5 registrado) e `intra_oos`. Interpretação **I11** (FE de hora, não de bloco): PENDENTE.
- Comparações: `saida/comparacao_medidas_anteriores.csv`. Relatório: `relatorio.md`.
- Hashes (sha256, 16 primeiros): livro 924f4365, contagem 8757eb00, agregacao bf1e5202, regressao ee8695c3, pipeline 489db140, extracao 2ea1b0c0, horarios a0d84588, passo2 de74f5a1, passo3 e542cb2c, passo4 5aea5aa3, passo5 22cfe69f, passo6 75ceb0a5, testes 38547476.
| 2026-10-01 | I11 (FE de hora na versão intradiária de 5 min) | Aprovada ("1. yes") |
| 2026-10-01 | Comparação com FQCR (H2/P2a) | Aprovada ("2. yes, lets start as in the thesis"): construir como em spec_hipoteses/spec_patches |
- Passo 6 ampliado: FQCR, BQCR, AggCancel, QCR (definições de spec_hipoteses, assinadas) × QIDres 5 min e horária; relatório seção 3b.

## Rodada 2 (instruções de 2026-10-01: "Próxima rodada da auditoria")

| Data | Ponto | Decisão |
|---|---|---|
| 2026-10-01 | Tarefa 1: dados disponíveis | Só existem os mesmos 12 pregões (09/2025, um trimestre). Usuário: **rodar nos 12 pregões**; relatório declara que é a MESMA amostra do piloto (não confirmatória); QIDres diária não calculável |
| 2026-10-01 | Tarefa 2: medidas | Principal: QIDres^R, QIDres^A, BQIDres, AQIDres na versão **intra_oos** (5 min, 7 pregões). Robustez: QIDres^R, QIDres^A (intra_oos) + **BQID e AQID brutos** (12 pregões). intra_spec NÃO entra na Tarefa 2 |
| 2026-10-01 | Bootstrap | B = 999 (instrução da rodada 2); piloto usou B = 2000 |

Piloto arquivado em `saida_piloto/` antes da nova execução.
| 2026-10-01 | Rodada 2 / Tarefa 1 | **Interrompida a pedido do usuário** durante o passo 3 ("please stop the process. We will try another test"); resultados do piloto seguem em `saida_piloto/` |

## Rodada 3 — Teste de viabilidade: existe informação a ser capturada no WIN? (instrução de 2026-10-01)

### PRÉ-REGISTRO (registrado em 2026-10-01 16:40:10, ANTES de qualquer execução desta rodada)

- **Confiabilidade da seleção adversa entre blocos (Passo 1):** Spearman-Brown ≥ 0,50 = "varia"; < 0,30 = "quase não varia"; entre 0,30 e 0,50 = "inconclusivo".
- **Parada condicional:** se a confiabilidade < 0,30 para todos os h ∈ {5, 30, 60} s, nas duas versões (AS bruto e AS sem média por horário), parar após o Passo 1.
- **Passo 2:** hipótese de sinal **β > 0** (QIDres maior → seleção adversa maior no bloco seguinte). Critério: IC 95% de β (wild cluster bootstrap por sessão, pesos de Webb, B = 9.999) exclui zero.
- Confirmatório: tudo na rodada, exceto θ_t e κ_t no Passo 2 (exploratórios, numeração E).
- Base de dados: os 12 pregões do piloto (pasta saida_piloto/); a rodada 2 foi interrompida.

### Desvio aprovado D-4
QIDres intradiária fora da amostra com **dia de controle = pregão anterior** (principal, 11 sessões). Versão do artigo (controle = 5 pregões antes, 7 sessões) como secundária. Aprovado na instrução da rodada 3.
| 2026-10-01 | Rodada 3, sinal do agressor | Usuário: **principal só eventos com sinal exato pelo book**; eventos só-RLP excluídos; robustez: incluir eventos só-RLP com sinal pela regra de cotação (p vs midpoint em t⁻); eventos mistos mantêm o sinal exato; p = VWAP de todos os prints do evento |

### Execução da rodada 3 (2026-10-01 17:31:43)
- Extração de negócios: 12 pregões; ~48% dos eventos com negócio são só-RLP (excluídos do principal). Base de ticks verificada igual à do piloto.
- Passo 1 (pré-registrado): Spearman-Brown 0,876–0,940 em todas as células ("varia"); parada condicional não acionada.
- **E1 (exploratório):** confiabilidade com metades sem sobreposição de horizonte (0–120 s vs 180–300 s): −0,076 a 0,023, ICs incluem zero. Motivo: pares/ímpares intercalados compartilham o caminho futuro do midpoint.
- Passo 2: 0 de 12 especificações confirmatórias preditivas com IC excluindo zero e β > 0. **E2 (exploratório):** θ_t e κ_t — alguns ICs excluem zero, sinal negativo.
- Passo 3: MDE (80%) ≈ 0,075–0,10 DP (controle = pregão anterior); ver saida/poder.csv.
- Testes: 20/20. Cobertura do IC wild (Webb, 12 clusters) em simulação: 34/40.
- Hashes (sha256, 8): f7e06c98 livro.py, 37c0a1ed pipeline.py, e4108a64 rodada3_negocios.py, 1f37776c rodada3_viabilidade.py, d391de51 testes/test_sinteticos.py, 

### Rodada 3b (instrução de 2026-10-01, toda exploratória; pré-registro inalterado)
- Seção 7 do relatório reorganizada a pedido: resultado pré-registrado mantido como estava (7.1); novas subseções 7.1a "Falha do estimador pré-registrado" e 7.1b E1 (exploratório). Nenhum número alterado.
- **E2 (exploratório) — robustez do E1** (2026-10-01 17:51:41): intervalo entre metades g ∈ {0, 30, 60, 120} s (bloco 5 min); blocos de 5, 10, 15 min com g = 60 s e metades proporcionais; estimador alternativo corr(AS_t, AS_{t+2}). Versões bruta e sem média por hora; IC wild cluster (Webb, B = 9.999). Saída: saida/robustez_E2_confiabilidade.csv. Célula g = 60 s, 5 min reproduz o E1.
- **E3 (exploratório) — θ e κ**: corr(θ_t, κ_t) por tamanho de bloco; equação do Passo 2 com θ_t e κ_t juntos, h = 30 e 60 s, blocos de 5, 10, 15 min; β, IC Webb e HAC, partial R² de cada. Saída: saida/robustez_E3_theta_kappa.csv. Nenhuma medida nova construída a partir de κ.
- Mudança de desempenho sem efeito em resultados: variaveis_estado só reordena estados se não estiverem em ordem temporal (testes 20/20).
- Hashes (sha256, 8): eff3aa18 rodada3b_robustez.py, f198a54d agregacao.py, 
- Relatório: nova seção 8. Fim da rodada 3b; parado conforme instrução.

### E4 (exploratório) — causalidade reversa do θ (2026-10-01 18:15:27)
- Instrução: último teste nos 12 pregões; depois, a amostra fica encerrada para exploração.
- Parâmetros: blocos 5/10/15 min; h = 30, 60 s; variáveis padronizadas (DP = 1) por amostra de regressão; IC Webb (wild cluster por sessão, B = 9.999, semente 20261006) e HAC (Newey-West, 5); IC da diferença de partial R² por bootstrap de pares por sessão (B = 9.999). Para frente com controles ampliados (AS_{t−1}, qvol_{t−1}, |r_{t−1}|); reação com |r_t|, |r_{t−1}|, AS_{t−1}, qvol_{t−1}, θ_{t−1..t−3}, volume_t, frac_1tick_t, EF de hora. Secundário: θ^B e θ^A com r com sinal.
- Painéis por L: saida/rodada3/painel_E4_{5,10,15}min.parquet. Resultado: saida/causalidade_reversa_theta.csv; relatório seção 9.
- Hash (sha256, 8): b60a622e rodada3c_theta.py
- **Amostra de 12 pregões encerrada para exploração.**
- **E4 — fechamento (2026-10-01 18:24:49, apenas descritivo, sem nova estimação e sem reabrir a amostra):** relatório seção 9.5 com (1) IC 95% da diferença entre partial R² (lido do CSV do E4; versão frente − reação = sinal trocado) e (2) tradução para unidades naturais: média e DP de θ_t (fração) e de AS (ticks) na amostra da equação para frente de cada célula; β × DP(AS_{t+1}) em ticks; efeito ÷ média de AS_{t+1}; ICs = ICs Webb de β × o mesmo fator. Saída: saida/E4_unidades_naturais.csv. Coeficientes de controles não exportados (instrução).
