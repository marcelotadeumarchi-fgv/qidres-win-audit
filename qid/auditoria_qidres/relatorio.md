# Relatório — Auditoria QIDres no WIN (WINV25, 15–30/09/2025)

Especificação: `auditoria_qidres_win.md`. Desvios e decisões: `run_log.md`. Sem veredictos interpretativos; a interpretação é do usuário.

> **⚠ Rodada 3 — resultados contrários ao pré-registro:** (1) Passo 2: em nenhuma das 12 especificações confirmatórias o IC 95% de β exclui zero com β > 0 (hipótese pré-registrada β > 0 não confirmada). (2) Passo 1: a confiabilidade pré-registrada é 0,88–0,94 ("varia"), mas o exploratório E1, com metades sem sobreposição de horizonte, dá ≈ 0 (−0,08 a 0,02; ICs incluem zero) — ver seção 7.1.

## ⚠ Resultado contrário à hipótese registrada

A especificação registrou que, se a hipótese da tese estiver certa, **D2 mostra R² alto** para QID^R em (1−θ)/(1+θ). Isso vale no diário (R² = 0,83, N = 12), mas **não em 5 min: R² = 0,36 [0,24; 0,48], N = 1.269** (limiar 0,80 não cruzado). Para QID^A (adicional), R² = 0,96 [0,92; 0,98] em 5 min.

## 1. Status dos testes

17/17 testes sintéticos passaram (8 da especificação + varredura fragmentada, RLP, deslocamento no mesmo evento, QIDres semanal-horária e 5 de integridade). Saída: `saida_testes_sinteticos.txt`. Motor de book validado contra `undercutting_valida.replay_dia`: 0 divergências de melhor bid/ask em 17.784.015 eventos (30/09).

## 2. Diagnósticos D1–D11

Tabela completa (todas as versões e variantes, com `obs` e limiares): `saida/audit_diagnostics.csv`. Abaixo, D1–D7 e as linhas base de D8–D11. ICs 95% por bootstrap de pregões (B = 2.000). Células com QID^U: **fracamente identificadas** (72,5% das horas sem undercut).

| id   | diagnostico                                       | nivel       |      valor |    ic95_inf |    ic95_sup | N                      | referencia_artigo                                           | cruzou_limiar           |
|:-----|:--------------------------------------------------|:------------|-----------:|------------:|------------:|:-----------------------|:------------------------------------------------------------|:------------------------|
| D1   | resíduo da identidade do spread                   | dia e 5 min |  0         | nan         | nan         | 12 dias + 1.269 blocos | Não reportado                                               | False                   |
| D2   | R² de qid_R em (1−θ)/(1+θ)                        | diario      |  0.8324    | nan         | nan         | 12                     | Não reportado                                               | True                    |
| D2   | R² de qid_A em (1−θ)/(1+θ)                        | diario      |  0.9852    | nan         | nan         | 12                     | Não reportado                                               | True                    |
| D2   | #Impr / #Deter (premissa da identidade)           | diario      |  1.005     |   1.003     |   1.006     | 12                     | Não reportado                                               | False                   |
| D2   | R² de qid_R em (1−θ)/(1+θ)                        | 5min        |  0.3563    |   0.2391    |   0.4778    | 1269                   | Não reportado                                               | False                   |
| D2   | R² de qid_A em (1−θ)/(1+θ)                        | 5min        |  0.9555    |   0.9159    |   0.9777    | 1269                   | Não reportado                                               | True                    |
| D2   | #Impr / #Deter (premissa da identidade)           | 5min        |  1.003     |   1.003     |   1.003     | 1269                   | Não reportado                                               | False                   |
| D3   | fração undercut, W=100 ms                         | amostra     |  0.004722  |   0.004018  |   0.005533  | 1724516                | Não reportado                                               | False                   |
| D3   | fração recomposicao, W=100 ms                     | amostra     |  0.6764    |   0.6585    |   0.6923    | 1724516                | Não reportado                                               | False                   |
| D3   | fração deslocamento, W=100 ms                     | amostra     |  0.3189    |   0.3036    |   0.336     | 1724516                | Não reportado                                               | False                   |
| D3   | fração undercut, W=1000 ms                        | amostra     |  0.0002047 |   0.0001326 |   0.0003135 | 1724516                | Não reportado                                               | True                    |
| D3   | fração recomposicao, W=1000 ms                    | amostra     |  0.8373    |   0.8233    |   0.8501    | 1724516                | Não reportado                                               | False                   |
| D3   | fração deslocamento, W=1000 ms                    | amostra     |  0.1625    |   0.1496    |   0.177     | 1724516                | Não reportado                                               | False                   |
| D3   | fração undercut, W=5000 ms                        | amostra     |  0         |   0         |   0         | 1724516                | Não reportado                                               | False                   |
| D3   | fração recomposicao, W=5000 ms                    | amostra     |  0.9209    |   0.9135    |   0.9275    | 1724516                | Não reportado                                               | False                   |
| D3   | fração deslocamento, W=5000 ms                    | amostra     |  0.07907   |   0.07242   |   0.08672   | 1724516                | Não reportado                                               | False                   |
| D4   | frac_1tick (média)                                | diario      |  0.9889    |   0.9868    |   0.9911    | 12                     | Corridas em ações restritas começam com spread ~2¢ (Fig. 3) | True                    |
| D4   | spread_tw_ticks (média)                           | diario      |  1.012     |   1.009     |   1.014     | 12                     | Corridas em ações restritas começam com spread ~2¢ (Fig. 3) | False                   |
| D4   | frac_1tick (média)                                | 5min        |  0.9889    |   0.9867    |   0.9911    | 1269                   | Corridas em ações restritas começam com spread ~2¢ (Fig. 3) | True                    |
| D4   | spread_tw_ticks (média)                           | 5min        |  1.012     |   1.009     |   1.014     | 1269                   | Corridas em ações restritas começam com spread ~2¢ (Fig. 3) | False                   |
| D5   | share_1tick_impr (média diária)                   | diario      |  0.9917    |   0.988     |   0.9945    | 12                     | 0,80 (Tabela 1, NBB)                                        | True                    |
| D5   | share_1tick_det_trade (média diária)              | diario      |  0.9886    |   0.9846    |   0.9918    | 12                     | 0,59 (Tabela 1, NBB)                                        | True                    |
| D6   | média de qid_R                                    | diario      |  0.02158   |   0.01938   |   0.0237    | 12                     | Média 0,61; mediana 0,63; 0,05% negativos (Tabela 1)        | True                    |
| D6   | média de qid_A                                    | diario      |  0.09184   |   0.08526   |   0.09798   | 12                     | Não se aplica                                               | False                   |
| D6   | média de qid_U                                    | diario      | -0.9995    |  -0.9997    |  -0.9992    | 12                     | Não se aplica                                               | False                   |
| D6   | média de qid_R                                    | 5min        |  0.02053   |   0.01878   |   0.0223    | 1269                   | Média 0,61; mediana 0,63; 0,05% negativos (Tabela 1)        | True                    |
| D6   | média de qid_A                                    | 5min        |  0.09067   |   0.08472   |   0.09656   | 1269                   | Não se aplica                                               | False                   |
| D6   | média de qid_U                                    | 5min        | -0.9979    |  -0.9986    |  -0.997     | 1269                   | Não se aplica                                               | False                   |
| D7   | concordância R 10 ms × A (execução)               | amostra     |  0.8732    |   0.8661    |   0.88      | 1715771                | Mediana de 5 ms entre negócio e deterioração (seção 3.1)    | True                    |
| D7   | concordância R 10 ms sem RLP × A (execução)       | amostra     |  0.8746    |   0.8677    |   0.8815    | 1715771                | Mediana de 5 ms entre negócio e deterioração (seção 3.1)    | True                    |
| D7   | concordância R 1 ms × A (execução)                | amostra     |  0.8761    |   0.8695    |   0.8826    | 1715771                | Mediana de 5 ms entre negócio e deterioração (seção 3.1)    | True                    |
| D8   | R² e b do passo 1 [qid_R|base] semana 2025-09-15  | hora        |  0.4881    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [qid_R|base] semana 2025-09-22  | hora        |  0.8508    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [qid_R|base] semana 2025-09-29  | hora        |  0.5188    | nan         | nan         | 20                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [qid_A|base] semana 2025-09-15  | hora        |  0.6612    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [qid_A|base] semana 2025-09-22  | hora        |  0.4925    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [qid_A|base] semana 2025-09-29  | hora        |  0.4598    | nan         | nan         | 20                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [qid_U|base] semana 2025-09-15  | hora        |  0.7595    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [qid_U|base] semana 2025-09-22  | hora        |  0.3855    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [qid_U|base] semana 2025-09-29  | hora        |  0.9059    | nan         | nan         | 20                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [bqid_R|base] semana 2025-09-15 | hora        |  0.3807    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [bqid_R|base] semana 2025-09-22 | hora        |  0.7899    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [bqid_R|base] semana 2025-09-29 | hora        |  0.5095    | nan         | nan         | 20                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [aqid_R|base] semana 2025-09-15 | hora        |  0.2915    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [aqid_R|base] semana 2025-09-22 | hora        |  0.8136    | nan         | nan         | 50                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D8   | R² e b do passo 1 [aqid_R|base] semana 2025-09-29 | hora        |  0.6455    | nan         | nan         | 20                     | R² 47,35% (Fig. E.1, entre ações)                           | False                   |
| D9   | DP QIDres [aqid_R|base]                           | hora        |  0.9932    |   0.4312    |   1.497     | 70                     | Média 0,08; DP 1,54 (Tabela 1)                              | False                   |
| D10  | ρ(QIDres [aqid_R|base], pct_spread_tw)            | hora        | -0.6583    |  -0.9186    |   0.3487    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | True                    |
| D10  | ρ(QIDres [aqid_R|base], volume)                   | hora        |  0.00447   |  -0.3419    |   0.2162    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | False                   |
| D10  | ρ(QIDres [aqid_R|base], qvol)                     | hora        | -0.6802    |  -0.9136    |   0.0002227 | 70                     | Próxima de zero (Tabela 1, Painel B)                        | True                    |
| D9   | DP QIDres [bqid_R|base]                           | hora        |  0.8825    |   0.647     |   1.035     | 70                     | Média 0,08; DP 1,54 (Tabela 1)                              | True                    |
| D10  | ρ(QIDres [bqid_R|base], pct_spread_tw)            | hora        | -0.3568    |  -0.577     |   0.01207   | 70                     | Próxima de zero (Tabela 1, Painel B)                        | True                    |
| D10  | ρ(QIDres [bqid_R|base], volume)                   | hora        |  0.2573    |   0.1047    |   0.4351    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | False                   |
| D10  | ρ(QIDres [bqid_R|base], qvol)                     | hora        | -0.2045    |  -0.4932    |   0.3484    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | False                   |
| D9   | DP QIDres [qid_A|base]                            | hora        |  0.9515    |   0.705     |   1.092     | 70                     | Média 0,08; DP 1,54 (Tabela 1)                              | True                    |
| D10  | ρ(QIDres [qid_A|base], pct_spread_tw)             | hora        | -0.2224    |  -0.4725    |   0.2584    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | False                   |
| D10  | ρ(QIDres [qid_A|base], volume)                    | hora        |  0.1714    |   0.05519   |   0.3414    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | False                   |
| D10  | ρ(QIDres [qid_A|base], qvol)                      | hora        | -0.2093    |  -0.4555    |   0.2669    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | False                   |
| D9   | DP QIDres [qid_R2|base]                           | hora        |  1.257     |   0.7719    |   1.559     | 70                     | Média 0,08; DP 1,54 (Tabela 1)                              | True                    |
| D10  | ρ(QIDres [qid_R2|base], pct_spread_tw)            | hora        | -0.3886    |  -0.7091    |   0.2374    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | True                    |
| D10  | ρ(QIDres [qid_R2|base], volume)                   | hora        |  0.1198    |   0.005576  |   0.2994    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | False                   |
| D10  | ρ(QIDres [qid_R2|base], qvol)                     | hora        | -0.3593    |  -0.6486    |   0.1519    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | True                    |
| D9   | DP QIDres [qid_R_1ms|1ms]                         | hora        |  1.621     |   0.7194    |   2.359     | 70                     | Média 0,08; DP 1,54 (Tabela 1)                              | True                    |
| D9   | DP QIDres [qid_R_semrlp|base]                     | hora        |  1.52      |   0.6998    |   2.17      | 70                     | Média 0,08; DP 1,54 (Tabela 1)                              | True                    |
| D10  | ρ(QIDres [qid_R_semrlp|base], pct_spread_tw)      | hora        | -0.6266    |  -0.8918    |   0.2724    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | True                    |
| D10  | ρ(QIDres [qid_R_semrlp|base], volume)             | hora        |  0.1105    |  -0.05478   |   0.2743    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | False                   |
| D10  | ρ(QIDres [qid_R_semrlp|base], qvol)               | hora        | -0.5829    |  -0.8424    |   0.07206   | 70                     | Próxima de zero (Tabela 1, Painel B)                        | True                    |
| D9   | DP QIDres [qid_R|base]                            | hora        |  1.635     |   0.6988    |   2.431     | 70                     | Média 0,08; DP 1,54 (Tabela 1)                              | True                    |
| D10  | ρ(QIDres [qid_R|base], pct_spread_tw)             | hora        | -0.667     |  -0.9002    |   0.2727    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | True                    |
| D10  | ρ(QIDres [qid_R|base], volume)                    | hora        |  0.1162    |  -0.05417   |   0.2758    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | False                   |
| D10  | ρ(QIDres [qid_R|base], qvol)                      | hora        | -0.6099    |  -0.8664    |   0.09405   | 70                     | Próxima de zero (Tabela 1, Painel B)                        | True                    |
| D9   | DP QIDres [qid_U|base]                            | hora        |  1.126     |   0.4519    |   1.751     | 70                     | Média 0,08; DP 1,54 (Tabela 1)                              | fracamente identificada |
| D10  | ρ(QIDres [qid_U|base], pct_spread_tw)             | hora        | -0.07306   |  -0.8199    |   0.6902    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | fracamente identificada |
| D10  | ρ(QIDres [qid_U|base], volume)                    | hora        |  0.1106    |  -0.5136    |   0.3513    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | fracamente identificada |
| D10  | ρ(QIDres [qid_U|base], qvol)                      | hora        |  0.2295    |  -0.1334    |   0.5169    | 70                     | Próxima de zero (Tabela 1, Painel B)                        | fracamente identificada |
| D11  | ρ(qid_R|base, qid_A|base)                         | hora        |  0.7527    |   0.6481    |   0.894     | 70                     | Não se aplica                                               | False                   |
| D11  | ρ(qid_R|base, qid_U|base)                         | hora        | -0.2654    |  -0.5914    |   0.2775    | 70                     | Não se aplica                                               | fracamente identificada |
| D11  | ρ(qid_A|base, qid_U|base)                         | hora        | -0.2302    |  -0.4717    |   0.1363    | 70                     | Não se aplica                                               | fracamente identificada |

Notas: D8 — o R² inclui efeitos fixos de horário e não é comparável aos 47,35% do artigo (corte transversal de ações); b > 0 em todas as semanas e versões. D9 — os alertas vêm do AR(1) dentro do dia (> 0,3). D10 — os ICs de QIDres^R × spread e × qvol incluem zero.

## 3. Correlação com as medidas dos testes anteriores

Principal (5 min): QIDres intradiária desta auditoria × `qid_res` da tese (`tese_apendice_a.py`, como foi rodada) e × `qid_signed` (H1 do protocolo; **não é uma QIDres**: é um desequilíbrio com sinal, por isso correlaciona com BQID/AQID com sinais opostos). Secundária: QIDres horária (desvio D-3) × médias horárias das duas séries. 7 pregões (22–30/09).

- `intra_spec`: passo 1 com o dia de controle (5 pregões antes) **e o próprio dia**, como a especificação/artigo — usa informação do próprio dia (conflito com a regra 5 do prompt; seguiu-se a especificação).
- `intra_oos`: passo 1 e S(QID) só do dia de controle (estritamente fora da amostra).

| nivel   | qidres_auditoria    | medida_anterior                 |   N |   n_dias |   pearson |   ic95_inf |   ic95_sup |   spearman | obs                       |
|:--------|:--------------------|:--------------------------------|----:|---------:|----------:|-----------:|-----------:|-----------:|:--------------------------|
| 5min    | aqid_R [intra_oos]  | qid_res da tese (5 min)         | 739 |        7 |     0.051 |      0.011 |      0.093 |      0.036 | nan                       |
| 5min    | aqid_R [intra_oos]  | qid_signed / H1 (5 min)         | 732 |        7 |     0.297 |      0.221 |      0.371 |      0.325 | nan                       |
| 5min    | aqid_R [intra_spec] | qid_res da tese (5 min)         | 739 |        7 |     0.046 |      0.022 |      0.07  |      0.041 | nan                       |
| 5min    | aqid_R [intra_spec] | qid_signed / H1 (5 min)         | 732 |        7 |     0.32  |      0.265 |      0.384 |      0.352 | nan                       |
| 5min    | bqid_R [intra_oos]  | qid_res da tese (5 min)         | 739 |        7 |     0.001 |     -0.113 |      0.103 |      0.025 | nan                       |
| 5min    | bqid_R [intra_oos]  | qid_signed / H1 (5 min)         | 732 |        7 |    -0.304 |     -0.409 |     -0.204 |     -0.302 | nan                       |
| 5min    | bqid_R [intra_spec] | qid_res da tese (5 min)         | 739 |        7 |    -0.002 |     -0.099 |      0.096 |      0.001 | nan                       |
| 5min    | bqid_R [intra_spec] | qid_signed / H1 (5 min)         | 732 |        7 |    -0.341 |     -0.419 |     -0.266 |     -0.33  | nan                       |
| 5min    | qid_A [intra_oos]   | qid_res da tese (5 min)         | 739 |        7 |     0.173 |     -0.008 |      0.322 |      0.16  | nan                       |
| 5min    | qid_A [intra_oos]   | qid_signed / H1 (5 min)         | 732 |        7 |     0.003 |     -0.071 |      0.053 |      0.043 | nan                       |
| 5min    | qid_A [intra_spec]  | qid_res da tese (5 min)         | 739 |        7 |     0.163 |      0.009 |      0.308 |      0.15  | nan                       |
| 5min    | qid_A [intra_spec]  | qid_signed / H1 (5 min)         | 732 |        7 |    -0.011 |     -0.068 |      0.046 |      0.025 | nan                       |
| 5min    | qid_R [intra_oos]   | qid_res da tese (5 min)         | 739 |        7 |     0.06  |     -0.12  |      0.225 |      0.072 | nan                       |
| 5min    | qid_R [intra_oos]   | qid_signed / H1 (5 min)         | 732 |        7 |     0.005 |     -0.09  |      0.102 |      0.068 | nan                       |
| 5min    | qid_R [intra_spec]  | qid_res da tese (5 min)         | 739 |        7 |     0.065 |     -0.103 |      0.22  |      0.059 | nan                       |
| 5min    | qid_R [intra_spec]  | qid_signed / H1 (5 min)         | 732 |        7 |    -0.007 |     -0.089 |      0.076 |      0.047 | nan                       |
| 5min    | qid_U [intra_oos]   | qid_res da tese (5 min)         | 739 |        7 |    -0.171 |     -0.207 |     -0.12  |     -0.1   | U fracamente identificada |
| 5min    | qid_U [intra_oos]   | qid_signed / H1 (5 min)         | 732 |        7 |    -0.101 |     -0.18  |     -0.027 |      0.085 | U fracamente identificada |
| 5min    | qid_U [intra_spec]  | qid_res da tese (5 min)         | 739 |        7 |    -0.183 |     -0.23  |     -0.118 |     -0.134 | U fracamente identificada |
| 5min    | qid_U [intra_spec]  | qid_signed / H1 (5 min)         | 732 |        7 |    -0.098 |     -0.184 |     -0.03  |      0.069 | U fracamente identificada |
| hora    | aqid_R|base         | qid_res da tese (média horária) |  70 |        7 |    -0.5   |     -0.743 |     -0.052 |     -0.256 | nan                       |
| hora    | bqid_R|base         | qid_res da tese (média horária) |  70 |        7 |    -0.043 |     -0.345 |      0.38  |      0.122 | nan                       |
| hora    | qid_A|base          | qid_res da tese (média horária) |  70 |        7 |    -0.049 |     -0.325 |      0.327 |      0.088 | nan                       |
| hora    | qid_R2|base         | qid_res da tese (média horária) |  70 |        7 |    -0.229 |     -0.503 |      0.197 |      0.002 | nan                       |
| hora    | qid_R_semrlp|base   | qid_res da tese (média horária) |  70 |        7 |    -0.395 |     -0.654 |      0.073 |     -0.065 | nan                       |
| hora    | qid_R|base          | qid_res da tese (média horária) |  70 |        7 |    -0.415 |     -0.666 |      0.08  |     -0.068 | nan                       |
| hora    | qid_U|base          | qid_res da tese (média horária) |  70 |        7 |    -0.127 |     -0.353 |      0.41  |      0.253 | U fracamente identificada |
| hora    | aqid_R|base         | qid_signed / H1 (média horária) |  70 |        7 |    -0.282 |     -0.622 |      0.147 |      0.221 | nan                       |
| hora    | bqid_R|base         | qid_signed / H1 (média horária) |  70 |        7 |    -0.063 |     -0.466 |      0.402 |      0.122 | nan                       |
| hora    | qid_A|base          | qid_signed / H1 (média horária) |  70 |        7 |    -0.016 |     -0.311 |      0.22  |      0.218 | nan                       |
| hora    | qid_R2|base         | qid_signed / H1 (média horária) |  70 |        7 |    -0.074 |     -0.465 |      0.321 |      0.257 | nan                       |
| hora    | qid_R_semrlp|base   | qid_signed / H1 (média horária) |  70 |        7 |    -0.198 |     -0.609 |      0.359 |      0.259 | nan                       |
| hora    | qid_R|base          | qid_signed / H1 (média horária) |  70 |        7 |    -0.212 |     -0.609 |      0.392 |      0.246 | nan                       |
| hora    | qid_U|base          | qid_signed / H1 (média horária) |  70 |        7 |     0.312 |     -0.29  |      0.55  |      0.048 | U fracamente identificada |

A especificação não fixou um limiar para "correlação baixa" no passo 6; nenhum foi aplicado.

### 3b. Medidas de fila de H2/P2a (FQCR, BQCR, AggCancel, QCR)

Mesmas definições de `spec_hipoteses.carregar` (assinadas: bid − ask; razão de somas do painel de 1 s, agregada a 5 min e a hora). Como `qid_signed`, são medidas com sinal, não QIDres.

| nivel   | qidres_auditoria    | medida_anterior                |   N |   n_dias |   pearson |   ic95_inf |   ic95_sup |   spearman | obs                       |
|:--------|:--------------------|:-------------------------------|----:|---------:|----------:|-----------:|-----------:|-----------:|:--------------------------|
| 5min    | aqid_R [intra_oos]  | FQCR / H2, P2a (5 min)         | 732 |        7 |    -0.67  |     -0.703 |     -0.644 |     -0.76  | nan                       |
| 5min    | aqid_R [intra_oos]  | BQCR / H2 (5 min)              | 732 |        7 |     0.236 |      0.174 |      0.284 |      0.257 | nan                       |
| 5min    | aqid_R [intra_oos]  | AggCancel / H2 (5 min)         | 732 |        7 |    -0.533 |     -0.623 |     -0.481 |     -0.586 | nan                       |
| 5min    | aqid_R [intra_oos]  | QCR / spec 2.4 (5 min)         | 732 |        7 |    -0.7   |     -0.74  |     -0.664 |     -0.774 | nan                       |
| 5min    | aqid_R [intra_spec] | FQCR / H2, P2a (5 min)         | 732 |        7 |    -0.71  |     -0.743 |     -0.683 |     -0.809 | nan                       |
| 5min    | aqid_R [intra_spec] | BQCR / H2 (5 min)              | 732 |        7 |     0.24  |      0.181 |      0.293 |      0.26  | nan                       |
| 5min    | aqid_R [intra_spec] | AggCancel / H2 (5 min)         | 732 |        7 |    -0.577 |     -0.645 |     -0.531 |     -0.639 | nan                       |
| 5min    | aqid_R [intra_spec] | QCR / spec 2.4 (5 min)         | 732 |        7 |    -0.735 |     -0.767 |     -0.704 |     -0.821 | nan                       |
| 5min    | bqid_R [intra_oos]  | FQCR / H2, P2a (5 min)         | 732 |        7 |     0.673 |      0.64  |      0.711 |      0.746 | nan                       |
| 5min    | bqid_R [intra_oos]  | BQCR / H2 (5 min)              | 732 |        7 |    -0.134 |     -0.245 |     -0.015 |     -0.153 | nan                       |
| 5min    | bqid_R [intra_oos]  | AggCancel / H2 (5 min)         | 732 |        7 |     0.596 |      0.54  |      0.657 |      0.626 | nan                       |
| 5min    | bqid_R [intra_oos]  | QCR / spec 2.4 (5 min)         | 732 |        7 |     0.687 |      0.638 |      0.735 |      0.749 | nan                       |
| 5min    | bqid_R [intra_spec] | FQCR / H2, P2a (5 min)         | 732 |        7 |     0.724 |      0.695 |      0.761 |      0.803 | nan                       |
| 5min    | bqid_R [intra_spec] | BQCR / H2 (5 min)              | 732 |        7 |    -0.18  |     -0.257 |     -0.097 |     -0.191 | nan                       |
| 5min    | bqid_R [intra_spec] | AggCancel / H2 (5 min)         | 732 |        7 |     0.626 |      0.576 |      0.68  |      0.665 | nan                       |
| 5min    | bqid_R [intra_spec] | QCR / spec 2.4 (5 min)         | 732 |        7 |     0.732 |      0.699 |      0.771 |      0.807 | nan                       |
| 5min    | qid_A [intra_oos]   | FQCR / H2, P2a (5 min)         | 732 |        7 |    -0.01  |     -0.063 |      0.041 |     -0.001 | nan                       |
| 5min    | qid_A [intra_oos]   | BQCR / H2 (5 min)              | 732 |        7 |     0.139 |      0.015 |      0.246 |      0.138 | nan                       |
| 5min    | qid_A [intra_oos]   | AggCancel / H2 (5 min)         | 732 |        7 |     0.074 |     -0.038 |      0.188 |      0.082 | nan                       |
| 5min    | qid_A [intra_oos]   | QCR / spec 2.4 (5 min)         | 732 |        7 |    -0.027 |     -0.068 |      0.02  |     -0.018 | nan                       |
| 5min    | qid_A [intra_spec]  | FQCR / H2, P2a (5 min)         | 732 |        7 |    -0.003 |     -0.084 |      0.074 |      0.006 | nan                       |
| 5min    | qid_A [intra_spec]  | BQCR / H2 (5 min)              | 732 |        7 |     0.11  |     -0.001 |      0.197 |      0.109 | nan                       |
| 5min    | qid_A [intra_spec]  | AggCancel / H2 (5 min)         | 732 |        7 |     0.054 |     -0.055 |      0.158 |      0.068 | nan                       |
| 5min    | qid_A [intra_spec]  | QCR / spec 2.4 (5 min)         | 732 |        7 |    -0.025 |     -0.098 |      0.048 |     -0.008 | nan                       |
| 5min    | qid_R [intra_oos]   | FQCR / H2, P2a (5 min)         | 732 |        7 |     0.002 |     -0.049 |      0.06  |      0.007 | nan                       |
| 5min    | qid_R [intra_oos]   | BQCR / H2 (5 min)              | 732 |        7 |     0.138 |      0.06  |      0.21  |      0.146 | nan                       |
| 5min    | qid_R [intra_oos]   | AggCancel / H2 (5 min)         | 732 |        7 |     0.086 |     -0.016 |      0.193 |      0.093 | nan                       |
| 5min    | qid_R [intra_oos]   | QCR / spec 2.4 (5 min)         | 732 |        7 |    -0.019 |     -0.059 |      0.026 |     -0.014 | nan                       |
| 5min    | qid_R [intra_spec]  | FQCR / H2, P2a (5 min)         | 732 |        7 |     0.015 |     -0.039 |      0.079 |      0.023 | nan                       |
| 5min    | qid_R [intra_spec]  | BQCR / H2 (5 min)              | 732 |        7 |     0.1   |      0.042 |      0.143 |      0.106 | nan                       |
| 5min    | qid_R [intra_spec]  | AggCancel / H2 (5 min)         | 732 |        7 |     0.078 |     -0.015 |      0.168 |      0.078 | nan                       |
| 5min    | qid_R [intra_spec]  | QCR / spec 2.4 (5 min)         | 732 |        7 |    -0.012 |     -0.063 |      0.05  |      0.008 | nan                       |
| 5min    | qid_U [intra_oos]   | FQCR / H2, P2a (5 min)         | 732 |        7 |     0.084 |     -0.1   |      0.215 |      0.011 | U fracamente identificada |
| 5min    | qid_U [intra_oos]   | BQCR / H2 (5 min)              | 732 |        7 |     0.04  |     -0.011 |      0.078 |      0.102 | U fracamente identificada |
| 5min    | qid_U [intra_oos]   | AggCancel / H2 (5 min)         | 732 |        7 |     0.11  |     -0.047 |      0.247 |      0.101 | U fracamente identificada |
| 5min    | qid_U [intra_oos]   | QCR / spec 2.4 (5 min)         | 732 |        7 |     0.075 |     -0.069 |      0.194 |     -0.013 | U fracamente identificada |
| 5min    | qid_U [intra_spec]  | FQCR / H2, P2a (5 min)         | 732 |        7 |     0.082 |     -0.13  |      0.277 |      0.011 | U fracamente identificada |
| 5min    | qid_U [intra_spec]  | BQCR / H2 (5 min)              | 732 |        7 |     0.033 |     -0.024 |      0.075 |      0.083 | U fracamente identificada |
| 5min    | qid_U [intra_spec]  | AggCancel / H2 (5 min)         | 732 |        7 |     0.113 |     -0.043 |      0.292 |      0.091 | U fracamente identificada |
| 5min    | qid_U [intra_spec]  | QCR / spec 2.4 (5 min)         | 732 |        7 |     0.081 |     -0.067 |      0.227 |     -0.012 | U fracamente identificada |
| hora    | aqid_R|base         | FQCR / H2, P2a (média horária) |  70 |        7 |     0.258 |     -0.124 |      0.562 |     -0.362 | nan                       |
| hora    | bqid_R|base         | FQCR / H2, P2a (média horária) |  70 |        7 |     0.066 |     -0.297 |      0.473 |      0.184 | nan                       |
| hora    | qid_A|base          | FQCR / H2, P2a (média horária) |  70 |        7 |     0.128 |     -0.15  |      0.427 |     -0.079 | nan                       |
| hora    | qid_R2|base         | FQCR / H2, P2a (média horária) |  70 |        7 |     0.086 |     -0.247 |      0.457 |     -0.127 | nan                       |
| hora    | qid_R_semrlp|base   | FQCR / H2, P2a (média horária) |  70 |        7 |     0.207 |     -0.187 |      0.569 |     -0.07  | nan                       |
| hora    | qid_R|base          | FQCR / H2, P2a (média horária) |  70 |        7 |     0.212 |     -0.24  |      0.583 |     -0.061 | nan                       |
| hora    | qid_U|base          | FQCR / H2, P2a (média horária) |  70 |        7 |    -0.184 |     -0.472 |      0.552 |      0.038 | U fracamente identificada |
| hora    | aqid_R|base         | BQCR / H2 (média horária)      |  70 |        7 |     0.252 |      0.057 |      0.408 |      0.277 | nan                       |
| hora    | bqid_R|base         | BQCR / H2 (média horária)      |  70 |        7 |     0.231 |     -0.128 |      0.437 |      0.288 | nan                       |
| hora    | qid_A|base          | BQCR / H2 (média horária)      |  70 |        7 |     0.29  |     -0.036 |      0.452 |      0.287 | nan                       |
| hora    | qid_R2|base         | BQCR / H2 (média horária)      |  70 |        7 |     0.333 |      0.092 |      0.476 |      0.316 | nan                       |
| hora    | qid_R_semrlp|base   | BQCR / H2 (média horária)      |  70 |        7 |     0.272 |      0.043 |      0.426 |      0.296 | nan                       |
| hora    | qid_R|base          | BQCR / H2 (média horária)      |  70 |        7 |     0.262 |      0.037 |      0.416 |      0.3   | nan                       |
| hora    | qid_U|base          | BQCR / H2 (média horária)      |  70 |        7 |    -0.111 |     -0.255 |      0.154 |      0.036 | U fracamente identificada |
| hora    | aqid_R|base         | AggCancel / H2 (média horária) |  70 |        7 |     0.37  |     -0.083 |      0.681 |      0.01  | nan                       |
| hora    | bqid_R|base         | AggCancel / H2 (média horária) |  70 |        7 |     0.15  |     -0.231 |      0.546 |      0.311 | nan                       |
| hora    | qid_A|base          | AggCancel / H2 (média horária) |  70 |        7 |     0.225 |     -0.074 |      0.518 |      0.194 | nan                       |
| hora    | qid_R2|base         | AggCancel / H2 (média horária) |  70 |        7 |     0.204 |     -0.145 |      0.543 |      0.185 | nan                       |
| hora    | qid_R_semrlp|base   | AggCancel / H2 (média horária) |  70 |        7 |     0.325 |     -0.109 |      0.699 |      0.233 | nan                       |
| hora    | qid_R|base          | AggCancel / H2 (média horária) |  70 |        7 |     0.331 |     -0.118 |      0.704 |      0.236 | nan                       |
| hora    | qid_U|base          | AggCancel / H2 (média horária) |  70 |        7 |    -0.209 |     -0.488 |      0.528 |      0.104 | U fracamente identificada |
| hora    | aqid_R|base         | QCR / spec 2.4 (média horária) |  70 |        7 |     0.381 |     -0.139 |      0.73  |     -0.409 | nan                       |
| hora    | bqid_R|base         | QCR / spec 2.4 (média horária) |  70 |        7 |     0.198 |     -0.213 |      0.586 |      0.156 | nan                       |
| hora    | qid_A|base          | QCR / spec 2.4 (média horária) |  70 |        7 |     0.175 |     -0.155 |      0.484 |     -0.168 | nan                       |
| hora    | qid_R2|base         | QCR / spec 2.4 (média horária) |  70 |        7 |     0.19  |     -0.235 |      0.597 |     -0.189 | nan                       |
| hora    | qid_R_semrlp|base   | QCR / spec 2.4 (média horária) |  70 |        7 |     0.356 |     -0.163 |      0.743 |     -0.124 | nan                       |
| hora    | qid_R|base          | QCR / spec 2.4 (média horária) |  70 |        7 |     0.37  |     -0.19  |      0.755 |     -0.115 | nan                       |
| hora    | qid_U|base          | QCR / spec 2.4 (média horária) |  70 |        7 |    -0.133 |     -0.412 |      0.532 |      0.139 | U fracamente identificada |

## 4. Dúvidas e desvios

Desvios aprovados (detalhe em `run_log.md`):

- **D-1′** Evento de casamento = pacote + continuações enquanto o volume não RLP não estiver todo executado no book, mesmo trocando o ms, teto 100 ms (a B3 publica agressões grandes em vários `msg_seq_num` e às vezes em ms consecutivos; `last_fragment` vem nulo).
- **D-2** Prints RLP (~25% do volume, sem ordem visível): contam como negócio na versão R; variante R sem RLP; nunca são execução na versão A.
- **D-3** Amostra de 12 pregões em um trimestre: QIDres com unidade **hora** e parâmetros/S(QID) da **semana anterior**, com efeitos fixos de horário.
- Interpretações aprovadas: I0–I10 (`msg_seq_num`, janelas de recomposição/deslocamento, R com Δt = 0, reset na referência, BQID/AQID pela definição R, grade de 5 min, grade horária, Rolling de 45 horas).

Pontos em aberto / limitações:

1. Interpretação **I11** (aprovada): efeitos fixos de **hora** na versão intradiária de 5 min (FE por bloco de 5 min saturaria o passo 1).
2. `intra_spec` usa o próprio dia no passo 1 (como o artigo); `intra_oos` é a alternativa sem leitura de futuro.
3. Anomalias remanescentes por pregão: 3–8,5 mil eventos com execução atribuída além do volume (saldo negativo) e número semelhante de conflitos de causa (execução no evento, último redutor = cancelamento) — `saida/passo3_info_por_dia.csv`.
4. N pequeno: 70 horas e 739 blocos de 5 min com QIDres; ICs largos.
5. Comparação com FQCR/BQCR/AggCancel/QCR (H2/P2a) feita: seção 3b.

## 7. Rodada 3 — Teste de viabilidade: existe informação a ser capturada no WIN?

Pré-registro (no `run_log.md`, antes de rodar): confiabilidade ≥ 0,50 "varia", < 0,30 "quase não varia"; Passo 2: β > 0 com IC 95% excluindo zero. 12 pregões; QIDres fora da amostra com controle = pregão anterior (D-4, 11 sessões) e = 5 pregões antes (7 sessões). Sinal do agressor exato pelo book (principal); eventos só-RLP pela regra de cotação (robustez). Eventos de casamento com negócio por pregão: 1,405,060–2,375,281; só-RLP ≈ metade (48.8%).

Nota de método: em simulação com 12 clusters, o IC por wild cluster bootstrap (Webb) cobriu o β verdadeiro em 34/40 réplicas (≈ 85%, abaixo de 95%); os ICs desta seção podem ser estreitos demais.

### 7.1 Passo 1 — Confiabilidade da seleção adversa entre blocos (pré-registrada)

| amostra                    |   h_s | versao             |   N_blocos |   n_sessoes |   r_metades |   spearman_brown |   ic95_inf |   ic95_sup | classificacao_preregistrada   | frac_var_AS_por_hora   |
|:---------------------------|------:|:-------------------|-----------:|------------:|------------:|-----------------:|-----------:|-----------:|:------------------------------|:-----------------------|
| principal                  |     5 | bruto              |       1269 |          12 |       0.783 |            0.878 |      0.842 |      0.909 | varia                         | 0.021                  |
| principal                  |     5 | sem_media_por_hora |       1269 |          12 |       0.779 |            0.876 |      0.843 |      0.905 | varia                         |                        |
| principal                  |    30 | bruto              |       1269 |          12 |       0.869 |            0.93  |      0.914 |      0.946 | varia                         | 0.018                  |
| principal                  |    30 | sem_media_por_hora |       1269 |          12 |       0.867 |            0.929 |      0.913 |      0.945 | varia                         |                        |
| principal                  |    60 | bruto              |       1269 |          12 |       0.867 |            0.929 |      0.911 |      0.945 | varia                         | 0.011                  |
| principal                  |    60 | sem_media_por_hora |       1269 |          12 |       0.867 |            0.929 |      0.912 |      0.945 | varia                         |                        |
| robustez_rlp_regra_cotacao |     5 | bruto              |       1269 |          12 |       0.83  |            0.907 |      0.885 |      0.928 | varia                         | 0.032                  |
| robustez_rlp_regra_cotacao |     5 | sem_media_por_hora |       1269 |          12 |       0.825 |            0.904 |      0.882 |      0.926 | varia                         |                        |
| robustez_rlp_regra_cotacao |    30 | bruto              |       1269 |          12 |       0.883 |            0.938 |      0.921 |      0.953 | varia                         | 0.021                  |
| robustez_rlp_regra_cotacao |    30 | sem_media_por_hora |       1269 |          12 |       0.881 |            0.937 |      0.92  |      0.952 | varia                         |                        |
| robustez_rlp_regra_cotacao |    60 | bruto              |       1269 |          12 |       0.886 |            0.94  |      0.926 |      0.953 | varia                         | 0.012                  |
| robustez_rlp_regra_cotacao |    60 | sem_media_por_hora |       1269 |          12 |       0.885 |            0.939 |      0.926 |      0.952 | varia                         |                        |

Pela regra pré-registrada, todas as células classificam como "varia"; a parada condicional não foi acionada e os Passos 2–3 foram executados.

### 7.1a Falha do estimador pré-registrado

O estimador pré-registrado divide os negócios de cada bloco em pares e ímpares pela ordem temporal. Negócios vizinhos ocorrem a milissegundos um do outro e, para h = 5, 30 e 60 s, o impacto de cada um, PI_h = d·(m_{t+h} − m_{t⁻}), usa praticamente a mesma trajetória futura do midpoint. As duas metades medem, portanto, o mesmo movimento de preço posterior aos negócios, e o erro de medida de uma metade não é independente do da outra. A fórmula de Spearman-Brown supõe erros independentes entre as metades; com a trajetória compartilhada, a correlação entre metades inclui o componente comum do movimento de preço e a confiabilidade fica inflada. O resultado da seção 7.1 permanece registrado como está.

### 7.1b E1 (exploratório, não pré-registrado)

Metades sem sobreposição de horizonte: 0–120 s vs 180–300 s do bloco de 5 min (intervalo de 60 s ≥ maior h), demais escolhas iguais ao Passo 1.

| id   | amostra   |   h_s | versao             |   N_blocos |   r_metades |   spearman_brown |   ic95_inf |   ic95_sup |
|:-----|:----------|------:|:-------------------|-----------:|------------:|-----------------:|-----------:|-----------:|
| E1   | principal |     5 | bruto              |       1269 |       0.011 |            0.023 |     -0.234 |      0.218 |
| E1   | principal |     5 | sem_media_por_hora |       1269 |       0.003 |            0.007 |     -0.228 |      0.195 |
| E1   | principal |    30 | bruto              |       1269 |       0.008 |            0.016 |     -0.149 |      0.155 |
| E1   | principal |    30 | sem_media_por_hora |       1269 |      -0     |           -0     |     -0.162 |      0.136 |
| E1   | principal |    60 | bruto              |       1269 |      -0.037 |           -0.076 |     -0.174 |      0.012 |
| E1   | principal |    60 | sem_media_por_hora |       1269 |      -0.027 |           -0.055 |     -0.154 |      0.034 |

### 7.2 Distribuição de PI_h e RS_h (ticks, por negócio)

| amostra                    |   h_s | medida   |   N_negocios |   media_pond_qtd |     dp |   frac_zero |    p5 |   p50 |   p95 |
|:---------------------------|------:|:---------|-------------:|-----------------:|-------:|------------:|------:|------:|------:|
| principal                  |     5 | PI       |     10514363 |            0.531 |  4.471 |       0.171 |  -6   |   0   |  6    |
| principal                  |     5 | RS       |     10514363 |            0.132 |  4.47  |       0.002 |  -5.5 |   0.5 |  6.5  |
| principal                  |    30 | PI       |     10510947 |            0.462 |  8.971 |       0.073 | -13   |   0   | 14    |
| principal                  |    30 | RS       |     10510947 |            0.201 |  8.971 |       0.001 | -13.5 |   0.5 | 13.5  |
| principal                  |    60 | PI       |     10505803 |            0.462 | 12.038 |       0.051 | -18   |   0   | 19    |
| principal                  |    60 | RS       |     10505803 |            0.2   | 12.037 |       0.001 | -18.5 |   0.5 | 18.5  |
| robustez_rlp_regra_cotacao |     5 | PI       |     20394945 |            0.428 |  4.049 |       0.181 |  -5.5 |   0   |  6    |
| robustez_rlp_regra_cotacao |     5 | RS       |     20394945 |            0.205 |  4.048 |       0.002 |  -5.5 |   0.5 |  6.15 |
| robustez_rlp_regra_cotacao |    30 | PI       |     20391105 |            0.369 |  8.552 |       0.074 | -13   |   0   | 13    |
| robustez_rlp_regra_cotacao |    30 | RS       |     20391105 |            0.264 |  8.552 |       0.001 | -12.5 |   0.5 | 13.5  |
| robustez_rlp_regra_cotacao |    60 | PI       |     20385483 |            0.372 | 11.607 |       0.052 | -18   |   0   | 19    |
| robustez_rlp_regra_cotacao |    60 | RS       |     20385483 |            0.26  | 11.606 |       0     | -18.5 |   0.5 | 18.5  |

Fração da variância de AS entre blocos explicada por efeitos fixos de hora: 1,1%–3,2% (coluna `frac_var_AS_por_hora` em 7.1).

### 7.3 Passo 2 — As medidas acompanham a seleção adversa? (θ e κ exploratórios)

Todas as variáveis padronizadas (DP = 1). IC principal: wild cluster bootstrap por sessão (Webb, B = 9.999); HAC (Newey-West, 5) secundário. Linhas "contemporânea" são descritivas.

| medida   | versao_qidres                             | status        |   h_s | tipo                       |   beta |   ic95_inf |   ic95_sup |   ic95_hac_inf |   ic95_hac_sup |   partial_R2_M |   N_blocos |   n_sessoes | exclui_zero   |
|:---------|:------------------------------------------|:--------------|------:|:---------------------------|-------:|-----------:|-----------:|---------------:|---------------:|---------------:|-----------:|------------:|:--------------|
| QIDres^R | controle=pregão anterior (principal, D-4) | confirmatorio |     5 | preditiva (t+1)            | -0.008 |     -0.07  |      0.052 |         -0.065 |          0.049 |         0.0001 |       1151 |          11 | False         |
| QIDres^R | controle=pregão anterior (principal, D-4) | confirmatorio |     5 | contemporânea (descritiva) | -0.056 |     -0.125 |      0.013 |         -0.117 |          0.005 |         0.0045 |       1163 |          11 | False         |
| QIDres^R | controle=pregão anterior (principal, D-4) | confirmatorio |    30 | preditiva (t+1)            | -0.008 |     -0.064 |      0.048 |         -0.058 |          0.042 |         0.0001 |       1151 |          11 | False         |
| QIDres^R | controle=pregão anterior (principal, D-4) | confirmatorio |    30 | contemporânea (descritiva) |  0.011 |     -0.028 |      0.049 |         -0.051 |          0.074 |         0.0002 |       1163 |          11 | False         |
| QIDres^R | controle=pregão anterior (principal, D-4) | confirmatorio |    60 | preditiva (t+1)            | -0.017 |     -0.067 |      0.034 |         -0.071 |          0.037 |         0.0003 |       1151 |          11 | False         |
| QIDres^R | controle=pregão anterior (principal, D-4) | confirmatorio |    60 | contemporânea (descritiva) |  0.007 |     -0.034 |      0.048 |         -0.062 |          0.075 |         0.0001 |       1163 |          11 | False         |
| QIDres^A | controle=pregão anterior (principal, D-4) | confirmatorio |     5 | preditiva (t+1)            | -0.042 |     -0.1   |      0.015 |         -0.099 |          0.014 |         0.0018 |       1151 |          11 | False         |
| QIDres^A | controle=pregão anterior (principal, D-4) | confirmatorio |     5 | contemporânea (descritiva) | -0.044 |     -0.091 |      0.002 |         -0.101 |          0.013 |         0.0029 |       1163 |          11 | False         |
| QIDres^A | controle=pregão anterior (principal, D-4) | confirmatorio |    30 | preditiva (t+1)            | -0.027 |     -0.087 |      0.033 |         -0.082 |          0.027 |         0.0007 |       1151 |          11 | False         |
| QIDres^A | controle=pregão anterior (principal, D-4) | confirmatorio |    30 | contemporânea (descritiva) | -0.034 |     -0.079 |      0.013 |         -0.09  |          0.022 |         0.0017 |       1163 |          11 | False         |
| QIDres^A | controle=pregão anterior (principal, D-4) | confirmatorio |    60 | preditiva (t+1)            | -0.045 |     -0.111 |      0.021 |         -0.105 |          0.015 |         0.002  |       1151 |          11 | False         |
| QIDres^A | controle=pregão anterior (principal, D-4) | confirmatorio |    60 | contemporânea (descritiva) | -0.052 |     -0.107 |      0.001 |         -0.113 |          0.008 |         0.0039 |       1163 |          11 | False         |
| QIDres^R | controle=5 pregões antes (artigo)         | confirmatorio |     5 | preditiva (t+1)            |  0.053 |     -0.076 |      0.183 |         -0.034 |          0.14  |         0.0029 |        731 |           7 | False         |
| QIDres^R | controle=5 pregões antes (artigo)         | confirmatorio |     5 | contemporânea (descritiva) |  0.005 |     -0.056 |      0.067 |         -0.05  |          0.061 |         0      |        739 |           7 | False         |
| QIDres^R | controle=5 pregões antes (artigo)         | confirmatorio |    30 | preditiva (t+1)            |  0.047 |     -0.037 |      0.13  |         -0.041 |          0.135 |         0.0023 |        731 |           7 | False         |
| QIDres^R | controle=5 pregões antes (artigo)         | confirmatorio |    30 | contemporânea (descritiva) |  0.016 |     -0.05  |      0.08  |         -0.043 |          0.075 |         0.0004 |        739 |           7 | False         |
| QIDres^R | controle=5 pregões antes (artigo)         | confirmatorio |    60 | preditiva (t+1)            |  0.007 |     -0.04  |      0.054 |         -0.071 |          0.085 |         0.0001 |        731 |           7 | False         |
| QIDres^R | controle=5 pregões antes (artigo)         | confirmatorio |    60 | contemporânea (descritiva) | -0.026 |     -0.066 |      0.013 |         -0.076 |          0.023 |         0.0012 |        739 |           7 | False         |
| QIDres^A | controle=5 pregões antes (artigo)         | confirmatorio |     5 | preditiva (t+1)            | -0.004 |     -0.139 |      0.129 |         -0.08  |          0.072 |         0      |        731 |           7 | False         |
| QIDres^A | controle=5 pregões antes (artigo)         | confirmatorio |     5 | contemporânea (descritiva) |  0.006 |     -0.064 |      0.075 |         -0.057 |          0.069 |         0      |        739 |           7 | False         |
| QIDres^A | controle=5 pregões antes (artigo)         | confirmatorio |    30 | preditiva (t+1)            |  0.022 |     -0.062 |      0.106 |         -0.051 |          0.095 |         0.0005 |        731 |           7 | False         |
| QIDres^A | controle=5 pregões antes (artigo)         | confirmatorio |    30 | contemporânea (descritiva) |  0.017 |     -0.046 |      0.079 |         -0.04  |          0.074 |         0.0005 |        739 |           7 | False         |
| QIDres^A | controle=5 pregões antes (artigo)         | confirmatorio |    60 | preditiva (t+1)            | -0.026 |     -0.095 |      0.043 |         -0.095 |          0.043 |         0.0006 |        731 |           7 | False         |
| QIDres^A | controle=5 pregões antes (artigo)         | confirmatorio |    60 | contemporânea (descritiva) | -0.029 |     -0.067 |      0.011 |         -0.079 |          0.022 |         0.0013 |        739 |           7 | False         |
| θ_t (E)  | n/a                                       | exploratorio  |     5 | preditiva (t+1)            | -0.045 |     -0.102 |      0.012 |         -0.101 |          0.011 |         0.0019 |       1256 |          12 | False         |
| θ_t (E)  | n/a                                       | exploratorio  |     5 | contemporânea (descritiva) | -0.004 |     -0.064 |      0.057 |         -0.058 |          0.051 |         0      |       1269 |          12 | False         |
| θ_t (E)  | n/a                                       | exploratorio  |    30 | preditiva (t+1)            | -0.056 |     -0.11  |     -0.004 |         -0.113 |          0.001 |         0.003  |       1256 |          12 | True          |
| θ_t (E)  | n/a                                       | exploratorio  |    30 | contemporânea (descritiva) | -0.056 |     -0.114 |      0.003 |         -0.106 |         -0.005 |         0.0041 |       1269 |          12 | False         |
| θ_t (E)  | n/a                                       | exploratorio  |    60 | preditiva (t+1)            | -0.07  |     -0.136 |     -0.003 |         -0.129 |         -0.011 |         0.0045 |       1256 |          12 | True          |
| θ_t (E)  | n/a                                       | exploratorio  |    60 | contemporânea (descritiva) | -0.068 |     -0.123 |     -0.013 |         -0.119 |         -0.017 |         0.006  |       1269 |          12 | True          |
| κ_t (E)  | n/a                                       | exploratorio  |     5 | preditiva (t+1)            | -0.05  |     -0.154 |      0.054 |         -0.17  |          0.071 |         0.0015 |       1256 |          12 | False         |
| κ_t (E)  | n/a                                       | exploratorio  |     5 | contemporânea (descritiva) |  0.119 |      0.021 |      0.213 |          0.029 |          0.208 |         0.0086 |       1269 |          12 | True          |
| κ_t (E)  | n/a                                       | exploratorio  |    30 | preditiva (t+1)            | -0.095 |     -0.185 |     -0.003 |         -0.199 |          0.01  |         0.0055 |       1256 |          12 | True          |
| κ_t (E)  | n/a                                       | exploratorio  |    30 | contemporânea (descritiva) | -0.129 |     -0.21  |     -0.047 |         -0.229 |         -0.03  |         0.0102 |       1269 |          12 | True          |
| κ_t (E)  | n/a                                       | exploratorio  |    60 | preditiva (t+1)            | -0.079 |     -0.146 |     -0.013 |         -0.165 |          0.006 |         0.0038 |       1256 |          12 | True          |
| κ_t (E)  | n/a                                       | exploratorio  |    60 | contemporânea (descritiva) | -0.141 |     -0.19  |     -0.093 |         -0.244 |         -0.038 |         0.0121 |       1269 |          12 | True          |

Especificações confirmatórias preditivas com IC 95% totalmente acima de zero (β > 0): 0 de 12.

### 7.4 Passo 3 — Poder

| medida   | versao_qidres                             | status        |   h_s |   n_sessoes |   N_blocos |   blocos_por_sessao |   icc_residuo_sessao |   efeito_desenho |   ep_wild |   beta_min_detectavel_80 |   sessoes_para_beta_0.10 |   sessoes_para_beta_0.20 |
|:---------|:------------------------------------------|:--------------|------:|------------:|-----------:|--------------------:|---------------------:|-----------------:|----------:|-------------------------:|-------------------------:|-------------------------:|
| QIDres^R | controle=pregão anterior (principal, D-4) | confirmatorio |     5 |          11 |       1151 |             104.636 |                0.005 |            1.546 |     0.031 |                    0.088 |                        9 |                        3 |
| QIDres^R | controle=pregão anterior (principal, D-4) | confirmatorio |    30 |          11 |       1151 |             104.636 |                0.007 |            1.744 |     0.029 |                    0.081 |                        8 |                        2 |
| QIDres^R | controle=pregão anterior (principal, D-4) | confirmatorio |    60 |          11 |       1151 |             104.636 |                0.011 |            2.117 |     0.027 |                    0.075 |                        7 |                        2 |
| QIDres^A | controle=pregão anterior (principal, D-4) | confirmatorio |     5 |          11 |       1151 |             104.636 |                0.005 |            1.553 |     0.03  |                    0.084 |                        8 |                        2 |
| QIDres^A | controle=pregão anterior (principal, D-4) | confirmatorio |    30 |          11 |       1151 |             104.636 |                0.008 |            1.782 |     0.031 |                    0.087 |                        9 |                        3 |
| QIDres^A | controle=pregão anterior (principal, D-4) | confirmatorio |    60 |          11 |       1151 |             104.636 |                0.012 |            2.224 |     0.037 |                    0.103 |                       12 |                        3 |
| QIDres^R | controle=5 pregões antes (artigo)         | confirmatorio |     5 |           7 |        731 |             104.429 |                0.015 |            2.524 |     0.067 |                    0.187 |                       25 |                        7 |
| QIDres^R | controle=5 pregões antes (artigo)         | confirmatorio |    30 |           7 |        731 |             104.429 |                0.006 |            1.616 |     0.044 |                    0.123 |                       11 |                        3 |
| QIDres^R | controle=5 pregões antes (artigo)         | confirmatorio |    60 |           7 |        731 |             104.429 |                0.012 |            2.236 |     0.025 |                    0.07  |                        4 |                        1 |
| QIDres^A | controle=5 pregões antes (artigo)         | confirmatorio |     5 |           7 |        731 |             104.429 |                0.012 |            2.261 |     0.07  |                    0.196 |                       27 |                        7 |
| QIDres^A | controle=5 pregões antes (artigo)         | confirmatorio |    30 |           7 |        731 |             104.429 |                0.005 |            1.533 |     0.044 |                    0.124 |                       11 |                        3 |
| QIDres^A | controle=5 pregões antes (artigo)         | confirmatorio |    60 |           7 |        731 |             104.429 |                0.012 |            2.196 |     0.036 |                    0.102 |                        8 |                        2 |
| θ_t (E)  | n/a                                       | exploratorio  |     5 |          12 |       1256 |             104.667 |                0.003 |            1.284 |     0.029 |                    0.082 |                        9 |                        3 |
| θ_t (E)  | n/a                                       | exploratorio  |    30 |          12 |       1256 |             104.667 |                0.004 |            1.442 |     0.028 |                    0.077 |                        8 |                        2 |
| θ_t (E)  | n/a                                       | exploratorio  |    60 |          12 |       1256 |             104.667 |                0.008 |            1.851 |     0.035 |                    0.099 |                       12 |                        3 |
| κ_t (E)  | n/a                                       | exploratorio  |     5 |          12 |       1256 |             104.667 |                0.005 |            1.496 |     0.058 |                    0.163 |                       32 |                        8 |
| κ_t (E)  | n/a                                       | exploratorio  |    30 |          12 |       1256 |             104.667 |                0.007 |            1.722 |     0.049 |                    0.139 |                       24 |                        6 |
| κ_t (E)  | n/a                                       | exploratorio  |    60 |          12 |       1256 |             104.667 |                0.01  |            2.07  |     0.035 |                    0.098 |                       12 |                        3 |

Hipóteses do cálculo: o menor β detectável é (z_0,975 + z_0,80)·EP, com o EP de β estimado pelo desvio-padrão das réplicas do wild cluster bootstrap por sessão, que já incorpora a correlação dentro da sessão; a correlação intraclasse dos resíduos por sessão e o efeito de desenho 1 + (m − 1)·ICC são reportados como descrição, não entram de novo no cálculo. O número de sessões necessário supõe o mesmo número de blocos por sessão (~105), a mesma variância residual e a mesma correlação intraclasse, de modo que o EP cai com 1/√G (G = número de sessões): G* = G·(MDE/β_alvo)². O cálculo é condicional à variância de AS observada; como o E1 indica que AS por bloco é dominado por ruído (confiabilidade ≈ 0 sem sobreposição de horizonte), o poder para detectar relação com o componente persistente de seleção adversa pode estar superestimado. A cobertura ≈ 85% do IC wild com 12 clusters sugere EPs subestimados, o que também torna o MDE otimista.

## 8. Rodada 3b — Robustez exploratória do E1 e de θ/κ (E2, E3)

Tudo nesta seção é exploratório; o pré-registro da rodada 3 não foi alterado. Amostra principal (sinal do agressor exato pelo book), 12 pregões. IC 95% por wild cluster bootstrap por sessão (pesos de Webb, B = 9.999); em simulação com 12 clusters esse IC cobriu o valor verdadeiro em ≈ 85% das réplicas. Grade de blocos de 10 e 15 min alinhada ao relógio, só blocos inteiros dentro da janela válida, descartando os que cruzam exclusões.

### 8.1 E2 — Confiabilidade sem sobreposição: intervalo entre as metades (bloco de 5 min)

Metades: [0, (L − g)/2) e [L − (L − g)/2, L) do bloco. Valor = Spearman-Brown; IC transformado a partir do IC da correlação entre metades. A linha g = 60 s reproduz o E1.

|   bloco_min |   intervalo_s |   h_s | versao             |   valor |   ic95_inf |   ic95_sup |   N_blocos |   n_sessoes | negocios_medios_por_metade   |
|------------:|--------------:|------:|:-------------------|--------:|-----------:|-----------:|-----------:|------------:|:-----------------------------|
|           5 |             0 |     5 | bruto              |   0.121 |     -0.087 |      0.288 |       1269 |          12 | 4,143                        |
|           5 |             0 |     5 | sem_media_por_hora |   0.112 |     -0.08  |      0.272 |       1269 |          12 | 4,143                        |
|           5 |            30 |     5 | bruto              |   0.063 |     -0.157 |      0.242 |       1269 |          12 | 3,735                        |
|           5 |            30 |     5 | sem_media_por_hora |   0.046 |     -0.161 |      0.216 |       1269 |          12 | 3,735                        |
|           5 |            60 |     5 | bruto              |   0.023 |     -0.225 |      0.223 |       1269 |          12 | 3,306                        |
|           5 |            60 |     5 | sem_media_por_hora |   0.007 |     -0.231 |      0.199 |       1269 |          12 | 3,306                        |
|           5 |           120 |     5 | bruto              |   0.131 |     -0.134 |      0.336 |       1269 |          12 | 2,495                        |
|           5 |           120 |     5 | sem_media_por_hora |   0.117 |     -0.126 |      0.311 |       1269 |          12 | 2,495                        |
|           5 |             0 |    30 | bruto              |   0.092 |     -0.092 |      0.247 |       1269 |          12 | 4,141                        |
|           5 |             0 |    30 | sem_media_por_hora |   0.082 |     -0.098 |      0.229 |       1269 |          12 | 4,141                        |
|           5 |            30 |    30 | bruto              |   0.06  |     -0.117 |      0.207 |       1269 |          12 | 3,734                        |
|           5 |            30 |    30 | sem_media_por_hora |   0.043 |     -0.125 |      0.187 |       1269 |          12 | 3,734                        |
|           5 |            60 |    30 | bruto              |   0.016 |     -0.149 |      0.161 |       1269 |          12 | 3,305                        |
|           5 |            60 |    30 | sem_media_por_hora |  -0     |     -0.162 |      0.135 |       1269 |          12 | 3,305                        |
|           5 |           120 |    30 | bruto              |   0.131 |     -0.019 |      0.261 |       1269 |          12 | 2,494                        |
|           5 |           120 |    30 | sem_media_por_hora |   0.114 |     -0.029 |      0.239 |       1269 |          12 | 2,494                        |
|           5 |             0 |    60 | bruto              |  -0.018 |     -0.196 |      0.134 |       1269 |          12 | 4,139                        |
|           5 |             0 |    60 | sem_media_por_hora |   0.003 |     -0.161 |      0.142 |       1269 |          12 | 4,139                        |
|           5 |            30 |    60 | bruto              |  -0.056 |     -0.183 |      0.055 |       1269 |          12 | 3,732                        |
|           5 |            30 |    60 | sem_media_por_hora |  -0.039 |     -0.162 |      0.071 |       1269 |          12 | 3,732                        |
|           5 |            60 |    60 | bruto              |  -0.076 |     -0.174 |      0.013 |       1269 |          12 | 3,303                        |
|           5 |            60 |    60 | sem_media_por_hora |  -0.055 |     -0.151 |      0.033 |       1269 |          12 | 3,303                        |
|           5 |           120 |    60 | bruto              |  -0.002 |     -0.106 |      0.093 |       1269 |          12 | 2,492                        |
|           5 |           120 |    60 | sem_media_por_hora |   0.007 |     -0.09  |      0.096 |       1269 |          12 | 2,492                        |

### 8.2 E2 — Tamanho do bloco (intervalo de 60 s, metades proporcionais)

|   bloco_min |   intervalo_s |   h_s | versao             |   valor |   ic95_inf |   ic95_sup |   N_blocos |   n_sessoes | negocios_medios_por_metade   |
|------------:|--------------:|------:|:-------------------|--------:|-----------:|-----------:|-----------:|------------:|:-----------------------------|
|           5 |            60 |     5 | bruto              |   0.023 |     -0.225 |      0.223 |       1269 |          12 | 3,306                        |
|           5 |            60 |     5 | sem_media_por_hora |   0.007 |     -0.231 |      0.199 |       1269 |          12 | 3,306                        |
|          10 |            60 |     5 | bruto              |   0.071 |     -0.192 |      0.274 |        634 |          12 | 7,442                        |
|          10 |            60 |     5 | sem_media_por_hora |   0.071 |     -0.214 |      0.295 |        634 |          12 | 7,442                        |
|          15 |            60 |     5 | bruto              |  -0.107 |     -0.505 |      0.184 |        407 |          12 | 11,584                       |
|          15 |            60 |     5 | sem_media_por_hora |  -0.081 |     -0.459 |      0.199 |        407 |          12 | 11,584                       |
|           5 |            60 |    30 | bruto              |   0.016 |     -0.149 |      0.161 |       1269 |          12 | 3,305                        |
|           5 |            60 |    30 | sem_media_por_hora |  -0     |     -0.162 |      0.135 |       1269 |          12 | 3,305                        |
|          10 |            60 |    30 | bruto              |   0.046 |     -0.105 |      0.176 |        634 |          12 | 7,439                        |
|          10 |            60 |    30 | sem_media_por_hora |   0.039 |     -0.107 |      0.164 |        634 |          12 | 7,439                        |
|          15 |            60 |    30 | bruto              |   0.022 |     -0.376 |      0.312 |        407 |          12 | 11,584                       |
|          15 |            60 |    30 | sem_media_por_hora |  -0     |     -0.358 |      0.267 |        407 |          12 | 11,584                       |
|           5 |            60 |    60 | bruto              |  -0.076 |     -0.174 |      0.013 |       1269 |          12 | 3,303                        |
|           5 |            60 |    60 | sem_media_por_hora |  -0.055 |     -0.151 |      0.033 |       1269 |          12 | 3,303                        |
|          10 |            60 |    60 | bruto              |   0.081 |     -0.221 |      0.307 |        634 |          12 | 7,435                        |
|          10 |            60 |    60 | sem_media_por_hora |   0.08  |     -0.225 |      0.306 |        634 |          12 | 7,435                        |
|          15 |            60 |    60 | bruto              |  -0.019 |     -0.262 |      0.181 |        407 |          12 | 11,584                       |
|          15 |            60 |    60 | sem_media_por_hora |   0.003 |     -0.272 |      0.219 |        407 |          12 | 11,584                       |

### 8.3 E2 — Estimador alternativo: correlação de AS entre os blocos t e t+2

Blocos consecutivos do mesmo pregão (pulando t+1). Valor = correlação de Pearson. A coluna de negócios mostra a média dos dois blocos do par.

|   bloco_min | intervalo_s   |   h_s | versao             |   valor |   ic95_inf |   ic95_sup |   N_blocos |   n_sessoes | negocios_medios_por_metade   |
|------------:|:--------------|------:|:-------------------|--------:|-----------:|-----------:|-----------:|------------:|:-----------------------------|
|           5 |               |     5 | bruto              |   0.039 |     -0.051 |      0.129 |       1243 |          12 | 8,266                        |
|           5 |               |     5 | sem_media_por_hora |   0.035 |     -0.053 |      0.123 |       1243 |          12 | 8,266                        |
|          10 |               |     5 | bruto              |   0.089 |     -0.033 |      0.207 |        608 |          12 | 16,463                       |
|          10 |               |     5 | sem_media_por_hora |   0.087 |     -0.035 |      0.204 |        608 |          12 | 16,463                       |
|          15 |               |     5 | bruto              |   0.019 |     -0.05  |      0.089 |        381 |          12 | 24,763                       |
|          15 |               |     5 | sem_media_por_hora |   0.016 |     -0.044 |      0.077 |        381 |          12 | 24,763                       |
|           5 |               |    30 | bruto              |   0.043 |     -0.024 |      0.111 |       1243 |          12 | 8,264                        |
|           5 |               |    30 | sem_media_por_hora |   0.031 |     -0.03  |      0.09  |       1243 |          12 | 8,264                        |
|          10 |               |    30 | bruto              |   0.01  |     -0.049 |      0.068 |        608 |          12 | 16,460                       |
|          10 |               |    30 | sem_media_por_hora |  -0.001 |     -0.068 |      0.064 |        608 |          12 | 16,460                       |
|          15 |               |    30 | bruto              |  -0.015 |     -0.083 |      0.051 |        381 |          12 | 24,763                       |
|          15 |               |    30 | sem_media_por_hora |  -0.032 |     -0.105 |      0.042 |        381 |          12 | 24,763                       |
|           5 |               |    60 | bruto              |   0.09  |     -0.003 |      0.18  |       1243 |          12 | 8,262                        |
|           5 |               |    60 | sem_media_por_hora |   0.081 |     -0.012 |      0.172 |       1243 |          12 | 8,262                        |
|          10 |               |    60 | bruto              |  -0.005 |     -0.093 |      0.084 |        608 |          12 | 16,456                       |
|          10 |               |    60 | sem_media_por_hora |  -0.014 |     -0.101 |      0.073 |        608 |          12 | 16,456                       |
|          15 |               |    60 | bruto              |  -0.005 |     -0.109 |      0.099 |        381 |          12 | 24,763                       |
|          15 |               |    60 | sem_media_por_hora |  -0.01  |     -0.123 |      0.102 |        381 |          12 | 24,763                       |

### 8.4 E3 — Correlação entre θ_t e κ_t

|   bloco_min |   correlacao |   ic95_webb_inf |   ic95_webb_sup |   N_blocos |   n_sessoes |
|------------:|-------------:|----------------:|----------------:|-----------:|------------:|
|           5 |        0.173 |           0.111 |           0.244 |       1269 |          12 |
|          10 |        0.155 |           0.068 |           0.25  |        634 |          12 |
|          15 |        0.062 |          -0.082 |           0.206 |        407 |          12 |

### 8.5 E3 — θ_t e κ_t juntos na equação do Passo 2

AS_{t+1,h} = α + β_θ·θ_t + β_κ·κ_t + γ·AS_{t,h} + controles (volume, qvol, |r_t|, frac_1tick) + efeitos fixos de hora; variáveis padronizadas (DP = 1). Partial R² de cada regressor = redução relativa da soma de quadrados ao incluí-lo no modelo que já contém o outro.

|   bloco_min |   h_s | regressor   |   beta |   ic95_webb_inf |   ic95_webb_sup |   ic95_hac_inf |   ic95_hac_sup |   partial_R2 |   N_blocos |   n_sessoes |
|------------:|------:|:------------|-------:|----------------:|----------------:|---------------:|---------------:|-------------:|-----------:|------------:|
|           5 |    30 | θ_t         | -0.054 |          -0.107 |          -0.002 |         -0.112 |          0.003 |       0.0028 |       1256 |          12 |
|           5 |    30 | κ_t         | -0.092 |          -0.183 |          -0.001 |         -0.196 |          0.011 |       0.0053 |       1256 |          12 |
|           5 |    60 | θ_t         | -0.068 |          -0.133 |          -0.002 |         -0.127 |         -0.009 |       0.0043 |       1256 |          12 |
|           5 |    60 | κ_t         | -0.077 |          -0.143 |          -0.01  |         -0.162 |          0.009 |       0.0036 |       1256 |          12 |
|          10 |    30 | θ_t         | -0.086 |          -0.143 |          -0.028 |         -0.157 |         -0.014 |       0.007  |        621 |          12 |
|          10 |    30 | κ_t         |  0.042 |          -0.034 |           0.118 |         -0.059 |          0.143 |       0.0014 |        621 |          12 |
|          10 |    60 | θ_t         | -0.092 |          -0.167 |          -0.016 |         -0.167 |         -0.017 |       0.0081 |        621 |          12 |
|          10 |    60 | κ_t         |  0.044 |          -0.092 |           0.18  |         -0.087 |          0.175 |       0.0015 |        621 |          12 |
|          15 |    30 | θ_t         | -0.089 |          -0.167 |          -0.01  |         -0.183 |          0.005 |       0.0076 |        394 |          12 |
|          15 |    30 | κ_t         |  0.086 |          -0.011 |           0.184 |         -0.006 |          0.177 |       0.0043 |        394 |          12 |
|          15 |    60 | θ_t         | -0.069 |          -0.181 |           0.042 |         -0.164 |          0.026 |       0.0046 |        394 |          12 |
|          15 |    60 | κ_t         |  0.035 |          -0.101 |           0.17  |         -0.083 |          0.154 |       0.0007 |        394 |          12 |

## 9. E4 — Causalidade reversa do θ (exploratório)

Último teste nos 12 pregões; nada aqui é confirmatório. Blocos de 5, 10 e 15 min; h = 30 e 60 s; mesmas exclusões de sessão e leilão; defasagens só entre blocos consecutivos do mesmo pregão; variáveis padronizadas (DP = 1) em cada amostra de regressão. IC Webb: wild cluster bootstrap por sessão (B = 9.999; cobertura ≈ 85% em simulação com 12 clusters). IC HAC: Newey-West, 5 defasagens. IC da diferença entre partial R²: bootstrap de pares reamostrando pregões inteiros (B = 9.999).

Para frente: AS_{t+1,h} = α + β·θ_t + γ₀·AS_{t,h} + γ₁·AS_{t−1,h} + volume_t + qvol_t + qvol_{t−1} + |r_t| + |r_{t−1}| + frac_1tick_t + EF de hora + ε.
Reação: θ_t = α + λ₀·|r_t| + λ₁·|r_{t−1}| + λ₂·AS_{t−1,h} + λ₃·qvol_{t−1} + θ_{t−1..t−3} + volume_t + frac_1tick_t + EF de hora + u.

### 9.1 Equação para frente: β de θ_t e comparação com o E3

O β do E3 vem do modelo com θ_t e κ_t juntos e controles do Passo 2; esta especificação usa os controles ampliados e não inclui κ_t.

|   bloco_min |   h_s |   β θ_t | IC Webb          | IC HAC           |   partial R² θ |   β E3 (mesma célula) | IC Webb E3       |    N |
|------------:|------:|--------:|:-----------------|:-----------------|---------------:|----------------------:|:-----------------|-----:|
|           5 |    30 |  -0.049 | [-0.103, 0.004]  | [-0.107, 0.009]  |         0.0023 |                -0.054 | [-0.107, -0.002] | 1243 |
|           5 |    60 |  -0.063 | [-0.133, 0.005]  | [-0.123, -0.003] |         0.0037 |                -0.068 | [-0.133, -0.002] | 1243 |
|          10 |    30 |  -0.094 | [-0.156, -0.032] | [-0.164, -0.025] |         0.0084 |                -0.086 | [-0.143, -0.028] |  608 |
|          10 |    60 |  -0.077 | [-0.171, 0.017]  | [-0.155, 0.000]  |         0.0057 |                -0.092 | [-0.167, -0.016] |  608 |
|          15 |    30 |  -0.085 | [-0.161, -0.008] | [-0.183, 0.012]  |         0.0069 |                -0.089 | [-0.167, -0.010] |  381 |
|          15 |    60 |  -0.072 | [-0.175, 0.030]  | [-0.171, 0.027]  |         0.005  |                -0.069 | [-0.181, 0.042]  |  381 |

### 9.2 Equação de reação: coeficientes λ

|   bloco_min |   h_s | coeficiente   |   estimativa | IC Webb         | IC HAC          |    N |
|------------:|------:|:--------------|-------------:|:----------------|:----------------|-----:|
|           5 |    30 | λ0 |r_t|      |       -0.035 | [-0.098, 0.028] | [-0.110, 0.040] | 1230 |
|           5 |    30 | λ1 |r_{t−1}|  |       -0.027 | [-0.064, 0.010] | [-0.066, 0.013] | 1230 |
|           5 |    30 | λ2 AS_{t−1}   |        0.002 | [-0.041, 0.044] | [-0.040, 0.044] | 1230 |
|           5 |    30 | λ3 qvol_{t−1} |        0.036 | [0.002, 0.071]  | [-0.007, 0.079] | 1230 |
|           5 |    60 | λ0 |r_t|      |       -0.035 | [-0.097, 0.028] | [-0.110, 0.040] | 1230 |
|           5 |    60 | λ1 |r_{t−1}|  |       -0.025 | [-0.077, 0.025] | [-0.069, 0.019] | 1230 |
|           5 |    60 | λ2 AS_{t−1}   |       -0.002 | [-0.058, 0.053] | [-0.043, 0.040] | 1230 |
|           5 |    60 | λ3 qvol_{t−1} |        0.037 | [0.007, 0.067]  | [-0.003, 0.078] | 1230 |
|          10 |    30 | λ0 |r_t|      |       -0.06  | [-0.151, 0.030] | [-0.171, 0.051] |  595 |
|          10 |    30 | λ1 |r_{t−1}|  |       -0.032 | [-0.076, 0.012] | [-0.084, 0.021] |  595 |
|          10 |    30 | λ2 AS_{t−1}   |       -0.028 | [-0.088, 0.031] | [-0.093, 0.036] |  595 |
|          10 |    30 | λ3 qvol_{t−1} |        0.026 | [-0.048, 0.099] | [-0.050, 0.101] |  595 |
|          10 |    60 | λ0 |r_t|      |       -0.061 | [-0.149, 0.029] | [-0.171, 0.050] |  595 |
|          10 |    60 | λ1 |r_{t−1}|  |       -0.029 | [-0.075, 0.016] | [-0.084, 0.026] |  595 |
|          10 |    60 | λ2 AS_{t−1}   |       -0.029 | [-0.063, 0.004] | [-0.080, 0.022] |  595 |
|          10 |    60 | λ3 qvol_{t−1} |        0.023 | [-0.055, 0.101] | [-0.052, 0.097] |  595 |
|          15 |    30 | λ0 |r_t|      |        0.046 | [-0.020, 0.111] | [-0.013, 0.104] |  369 |
|          15 |    30 | λ1 |r_{t−1}|  |        0.019 | [-0.052, 0.090] | [-0.071, 0.109] |  369 |
|          15 |    30 | λ2 AS_{t−1}   |        0.033 | [-0.032, 0.098] | [-0.047, 0.113] |  369 |
|          15 |    30 | λ3 qvol_{t−1} |       -0.047 | [-0.184, 0.087] | [-0.176, 0.083] |  369 |
|          15 |    60 | λ0 |r_t|      |        0.043 | [-0.021, 0.107] | [-0.017, 0.102] |  369 |
|          15 |    60 | λ1 |r_{t−1}|  |        0.046 | [-0.027, 0.118] | [-0.046, 0.138] |  369 |
|          15 |    60 | λ2 AS_{t−1}   |       -0.039 | [-0.084, 0.006] | [-0.107, 0.029] |  369 |
|          15 |    60 | λ3 qvol_{t−1} |       -0.017 | [-0.138, 0.104] | [-0.132, 0.097] |  369 |

### 9.3 Partial R²: frente × reação

|   bloco_min |   h_s |   partial R² frente (θ) |   partial R² reação (conjunto) |   diferença (reação − frente) | IC 95% da diferença   |
|------------:|------:|------------------------:|-------------------------------:|------------------------------:|:----------------------|
|           5 |    30 |                  0.0023 |                         0.0035 |                        0.0012 | [-0.0047, 0.0114]     |
|           5 |    60 |                  0.0037 |                         0.0035 |                       -0.0002 | [-0.0108, 0.0120]     |
|          10 |    30 |                  0.0084 |                         0.0073 |                       -0.0011 | [-0.0173, 0.0405]     |
|          10 |    60 |                  0.0057 |                         0.0074 |                        0.0017 | [-0.0235, 0.0413]     |
|          15 |    30 |                  0.0069 |                         0.0069 |                       -0.0001 | [-0.0137, 0.0466]     |
|          15 |    60 |                  0.005  |                         0.0078 |                        0.0028 | [-0.0152, 0.0407]     |

### 9.4 Secundário — reação por lado (θ^B, θ^A) com r com sinal

Mesma equação de reação, trocando |r_t| e |r_{t−1}| por r_t e r_{t−1} (retorno pelo midpoint dentro do bloco, em ticks, com sinal) e θ por θ^B (só deteriorações do bid) ou θ^A (só do ask).

|   bloco_min |   h_s | lado      | coeficiente   |   estimativa | IC Webb          | IC HAC           |   partial R² conjunto |    N |
|------------:|------:|:----------|:--------------|-------------:|:-----------------|:-----------------|----------------------:|-----:|
|           5 |    30 | θ^B (bid) | λ0 r_t        |       -0.11  | [-0.143, -0.077] | [-0.145, -0.074] |                0.0199 | 1230 |
|           5 |    30 | θ^B (bid) | λ1 r_{t−1}    |        0.017 | [-0.015, 0.049]  | [-0.016, 0.051]  |                0.0199 | 1230 |
|           5 |    30 | θ^B (bid) | λ2 AS_{t−1}   |       -0.003 | [-0.048, 0.043]  | [-0.049, 0.044]  |                0.0199 | 1230 |
|           5 |    30 | θ^B (bid) | λ3 qvol_{t−1} |        0.031 | [-0.016, 0.077]  | [-0.017, 0.079]  |                0.0199 | 1230 |
|           5 |    30 | θ^A (ask) | λ0 r_t        |        0.027 | [-0.011, 0.067]  | [-0.028, 0.083]  |                0.006  | 1230 |
|           5 |    30 | θ^A (ask) | λ1 r_{t−1}    |       -0.05  | [-0.076, -0.024] | [-0.085, -0.014] |                0.006  | 1230 |
|           5 |    30 | θ^A (ask) | λ2 AS_{t−1}   |       -0.006 | [-0.047, 0.036]  | [-0.048, 0.036]  |                0.006  | 1230 |
|           5 |    30 | θ^A (ask) | λ3 qvol_{t−1} |        0.033 | [-0.002, 0.067]  | [-0.012, 0.078]  |                0.006  | 1230 |
|           5 |    60 | θ^B (bid) | λ0 r_t        |       -0.11  | [-0.142, -0.078] | [-0.145, -0.074] |                0.0199 | 1230 |
|           5 |    60 | θ^B (bid) | λ1 r_{t−1}    |        0.017 | [-0.016, 0.050]  | [-0.016, 0.051]  |                0.0199 | 1230 |
|           5 |    60 | θ^B (bid) | λ2 AS_{t−1}   |       -0.007 | [-0.057, 0.044]  | [-0.048, 0.034]  |                0.0199 | 1230 |
|           5 |    60 | θ^B (bid) | λ3 qvol_{t−1} |        0.032 | [-0.011, 0.074]  | [-0.013, 0.077]  |                0.0199 | 1230 |
|           5 |    60 | θ^A (ask) | λ0 r_t        |        0.028 | [-0.010, 0.066]  | [-0.027, 0.083]  |                0.0064 | 1230 |
|           5 |    60 | θ^A (ask) | λ1 r_{t−1}    |       -0.05  | [-0.076, -0.024] | [-0.086, -0.014] |                0.0064 | 1230 |
|           5 |    60 | θ^A (ask) | λ2 AS_{t−1}   |       -0.019 | [-0.054, 0.017]  | [-0.056, 0.019]  |                0.0064 | 1230 |
|           5 |    60 | θ^A (ask) | λ3 qvol_{t−1} |        0.036 | [0.003, 0.070]   | [-0.007, 0.079]  |                0.0064 | 1230 |
|          10 |    30 | θ^B (bid) | λ0 r_t        |       -0.108 | [-0.173, -0.043] | [-0.180, -0.037] |                0.0214 |  595 |
|          10 |    30 | θ^B (bid) | λ1 r_{t−1}    |       -0.015 | [-0.065, 0.036]  | [-0.074, 0.043]  |                0.0214 |  595 |
|          10 |    30 | θ^B (bid) | λ2 AS_{t−1}   |       -0.03  | [-0.093, 0.034]  | [-0.109, 0.049]  |                0.0214 |  595 |
|          10 |    30 | θ^B (bid) | λ3 qvol_{t−1} |        0.037 | [-0.041, 0.116]  | [-0.045, 0.120]  |                0.0214 |  595 |
|          10 |    30 | θ^A (ask) | λ0 r_t        |        0.008 | [-0.046, 0.063]  | [-0.063, 0.079]  |                0.0058 |  595 |
|          10 |    30 | θ^A (ask) | λ1 r_{t−1}    |       -0.043 | [-0.093, 0.007]  | [-0.088, 0.001]  |                0.0058 |  595 |
|          10 |    30 | θ^A (ask) | λ2 AS_{t−1}   |       -0.031 | [-0.099, 0.038]  | [-0.098, 0.035]  |                0.0058 |  595 |
|          10 |    30 | θ^A (ask) | λ3 qvol_{t−1} |        0.013 | [-0.068, 0.095]  | [-0.061, 0.087]  |                0.0058 |  595 |
|          10 |    60 | θ^B (bid) | λ0 r_t        |       -0.109 | [-0.174, -0.046] | [-0.180, -0.038] |                0.0205 |  595 |
|          10 |    60 | θ^B (bid) | λ1 r_{t−1}    |       -0.017 | [-0.069, 0.034]  | [-0.075, 0.042]  |                0.0205 |  595 |
|          10 |    60 | θ^B (bid) | λ2 AS_{t−1}   |       -0.013 | [-0.062, 0.034]  | [-0.070, 0.043]  |                0.0205 |  595 |
|          10 |    60 | θ^B (bid) | λ3 qvol_{t−1} |        0.028 | [-0.062, 0.120]  | [-0.055, 0.110]  |                0.0205 |  595 |
|          10 |    60 | θ^A (ask) | λ0 r_t        |        0.009 | [-0.045, 0.063]  | [-0.062, 0.081]  |                0.0088 |  595 |
|          10 |    60 | θ^A (ask) | λ1 r_{t−1}    |       -0.042 | [-0.091, 0.007]  | [-0.087, 0.002]  |                0.0088 |  595 |
|          10 |    60 | θ^A (ask) | λ2 AS_{t−1}   |       -0.052 | [-0.090, -0.014] | [-0.105, 0.000]  |                0.0088 |  595 |
|          10 |    60 | θ^A (ask) | λ3 qvol_{t−1} |        0.02  | [-0.058, 0.098]  | [-0.051, 0.090]  |                0.0088 |  595 |
|          15 |    30 | θ^B (bid) | λ0 r_t        |       -0.063 | [-0.127, -0.001] | [-0.131, 0.005]  |                0.0104 |  369 |
|          15 |    30 | θ^B (bid) | λ1 r_{t−1}    |        0.015 | [-0.050, 0.082]  | [-0.049, 0.079]  |                0.0104 |  369 |
|          15 |    30 | θ^B (bid) | λ2 AS_{t−1}   |        0.045 | [-0.023, 0.115]  | [-0.043, 0.133]  |                0.0104 |  369 |
|          15 |    30 | θ^B (bid) | λ3 qvol_{t−1} |       -0.026 | [-0.161, 0.105]  | [-0.169, 0.117]  |                0.0104 |  369 |
|          15 |    30 | θ^A (ask) | λ0 r_t        |        0.032 | [-0.019, 0.084]  | [-0.026, 0.090]  |                0.0083 |  369 |
|          15 |    30 | θ^A (ask) | λ1 r_{t−1}    |        0.053 | [0.002, 0.106]   | [-0.007, 0.114]  |                0.0083 |  369 |
|          15 |    30 | θ^A (ask) | λ2 AS_{t−1}   |        0.002 | [-0.074, 0.079]  | [-0.067, 0.072]  |                0.0083 |  369 |
|          15 |    30 | θ^A (ask) | λ3 qvol_{t−1} |       -0.048 | [-0.194, 0.095]  | [-0.175, 0.080]  |                0.0083 |  369 |
|          15 |    60 | θ^B (bid) | λ0 r_t        |       -0.058 | [-0.116, 0.001]  | [-0.126, 0.009]  |                0.0098 |  369 |
|          15 |    60 | θ^B (bid) | λ1 r_{t−1}    |        0.023 | [-0.037, 0.083]  | [-0.039, 0.086]  |                0.0098 |  369 |
|          15 |    60 | θ^B (bid) | λ2 AS_{t−1}   |       -0.037 | [-0.093, 0.019]  | [-0.102, 0.028]  |                0.0098 |  369 |
|          15 |    60 | θ^B (bid) | λ3 qvol_{t−1} |        0.015 | [-0.101, 0.133]  | [-0.116, 0.145]  |                0.0098 |  369 |
|          15 |    60 | θ^A (ask) | λ0 r_t        |        0.035 | [-0.015, 0.087]  | [-0.023, 0.094]  |                0.0098 |  369 |
|          15 |    60 | θ^A (ask) | λ1 r_{t−1}    |        0.058 | [0.007, 0.109]   | [-0.002, 0.118]  |                0.0098 |  369 |
|          15 |    60 | θ^A (ask) | λ2 AS_{t−1}   |       -0.03  | [-0.087, 0.027]  | [-0.097, 0.037]  |                0.0098 |  369 |
|          15 |    60 | θ^A (ask) | λ3 qvol_{t−1} |       -0.035 | [-0.174, 0.105]  | [-0.153, 0.083]  |                0.0098 |  369 |

### 9.5 Diferença de partial R² e tradução para unidades naturais (descritivo, sem nova estimação)

Números derivados do CSV do E4 (`saida/causalidade_reversa_theta.csv`) e dos painéis do E4 (`saida/rodada3/painel_E4_{5,10,15}min.parquet`); nenhum modelo foi reestimado. Médias e desvios-padrão calculados na mesma amostra da equação para frente de cada célula (linhas com todas as variáveis disponíveis). Saída: `saida/E4_unidades_naturais.csv`.

**(1) IC 95% da diferença entre os partial R²** (bootstrap de pares por sessão, B = 9.999). A coluna "frente − reação" é a do CSV com o sinal trocado; o IC correspondente é o IC do CSV com os extremos trocados e o sinal invertido.

|   bloco_min |   h_s |   partial R² frente − reação | IC 95%             |   partial R² reação − frente (como no CSV) | IC 95%             |
|------------:|------:|-----------------------------:|:-------------------|-------------------------------------------:|:-------------------|
|           5 |    30 |                      -0.0012 | [-0.0114, +0.0047] |                                     0.0012 | [-0.0047, +0.0114] |
|           5 |    60 |                       0.0002 | [-0.0120, +0.0108] |                                    -0.0002 | [-0.0108, +0.0120] |
|          10 |    30 |                       0.0011 | [-0.0405, +0.0173] |                                    -0.0011 | [-0.0173, +0.0405] |
|          10 |    60 |                      -0.0017 | [-0.0413, +0.0235] |                                     0.0017 | [-0.0235, +0.0413] |
|          15 |    30 |                       0.0001 | [-0.0466, +0.0137] |                                    -0.0001 | [-0.0137, +0.0466] |
|          15 |    60 |                      -0.0028 | [-0.0407, +0.0152] |                                     0.0028 | [-0.0152, +0.0407] |

**(2a) θ_t e AS em unidades naturais.** θ em fração (0 a 1); AS em ticks (1 tick = 5 pontos). AS_{t+1} é a variável dependente da equação para frente; AS_t é o regressor contemporâneo.

|   bloco_min |   h_s |    N |   θ média |   θ DP |   AS_{t+1} média (ticks) |   AS_{t+1} DP (ticks) |   AS_t média (ticks) |   AS_t DP (ticks) |
|------------:|------:|-----:|----------:|-------:|-------------------------:|----------------------:|---------------------:|------------------:|
|           5 |    30 | 1243 |     0.836 |  0.03  |                    0.311 |                 0.83  |                0.304 |             0.817 |
|           5 |    60 | 1243 |     0.836 |  0.03  |                    0.285 |                 1.044 |                0.281 |             1.046 |
|          10 |    30 |  608 |     0.835 |  0.026 |                    0.369 |                 0.702 |                0.351 |             0.667 |
|          10 |    60 |  608 |     0.835 |  0.026 |                    0.335 |                 0.813 |                0.323 |             0.821 |
|          15 |    30 |  381 |     0.834 |  0.025 |                    0.378 |                 0.574 |                0.372 |             0.572 |
|          15 |    60 |  381 |     0.834 |  0.025 |                    0.348 |                 0.63  |                0.337 |             0.656 |

**(2b) β do E4 em ticks.** Efeito de +1 DP de θ_t sobre o impacto no bloco seguinte = β × DP(AS_{t+1} em ticks); fração do impacto médio = efeito em ticks ÷ média de AS_{t+1}. Os ICs são os ICs Webb de β multiplicados pelo mesmo fator (transformação linear; não incorporam a incerteza da média e do DP).

|   bloco_min |   h_s |   β (DP) |   efeito de +1 DP de θ (ticks) | IC 95% Webb (ticks)   |   efeito / AS médio | IC 95% Webb (fração)   |
|------------:|------:|---------:|-------------------------------:|:----------------------|--------------------:|:-----------------------|
|           5 |    30 |   -0.049 |                        -0.0407 | [-0.0858, +0.0036]    |              -0.131 | [-0.276, +0.012]       |
|           5 |    60 |   -0.063 |                        -0.0659 | [-0.1388, +0.0047]    |              -0.231 | [-0.486, +0.017]       |
|          10 |    30 |   -0.094 |                        -0.0662 | [-0.1098, -0.0228]    |              -0.18  | [-0.298, -0.062]       |
|          10 |    60 |   -0.077 |                        -0.0629 | [-0.1387, +0.0139]    |              -0.188 | [-0.415, +0.042]       |
|          15 |    30 |   -0.085 |                        -0.049  | [-0.0924, -0.0048]    |              -0.129 | [-0.244, -0.013]       |
|          15 |    60 |   -0.072 |                        -0.0456 | [-0.1105, +0.0188]    |              -0.131 | [-0.317, +0.054]       |

## Arquivos

`saida/causalidade_reversa_theta.csv`, `saida/E4_unidades_naturais.csv`, `saida/robustez_E2_confiabilidade.csv`, `saida/robustez_E3_theta_kappa.csv`, `saida/events_classified.parquet`, `qid_daily.parquet`, `qid_5min.parquet`, `qid_hourly.parquet`, `qidres_hourly.parquet` (no lugar de `qidres_daily.parquet`, desvio D-3), `qidres_5min_intradiaria.parquet`, `first_stage.csv`, `audit_diagnostics.csv`, `comparacao_medidas_anteriores.csv`, `tabela_horarios.csv`; `run_log.md`.
