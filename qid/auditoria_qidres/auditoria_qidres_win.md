# Auditoria QIDres no WIN — revisão e especificação

Oct 1, 2026 · @Marchi

> Cópia de referência (condensada nas tabelas de saída) da especificação, salva junto ao código em 2026-10-01.
> É a fonte de verdade da implementação. Desvios aprovados vão para `run_log.md`.

## Revisão do artigo

A QIDres é um resíduo padronizado de uma razão de contagens; no WIN, a razão tende a medir a composição das deteriorações (cancelamento contra execução), não o undercutting, e a auditoria precisa testar isso antes de qualquer conclusão.

**A medida (seção 3.2, eq. 1).** QID é o número de melhorias do NBBO menos o de deteriorações causadas por negócios, dividido pela soma das duas. Uma deterioração causada por negócio é a primeira deterioração em até 10 ms após um negócio. As contagens são feitas por lado (bid e ask) e somadas (nota da Tabela 1). Todas as melhorias entram, de um ou de vários ticks.

**O resíduo (seção 3.4, eqs. 2 e 3).** Em cada ação-trimestre, regride-se o QID diário no log do spread relativo cotado ponderado pelo tempo. O valor esperado do dia usa os parâmetros do trimestre *anterior*; o desvio é dividido pelo desvio padrão do QID do trimestre anterior e multiplicado por −1.

**A cadeia de identificação.** São quatro elos: corridas de undercutting existem nos dados (Figura 3); o QID mede undercutting, porque cai após desdobramentos que encarecem a melhoria (Figura 4); o resíduo remove a liquidez; e a medida sobe perto de negócios sabidamente informados e de eventos corporativos (seções 4 e 5).

**Pontos fortes.** A padronização é fora da amostra, o que evita viés de antecipação. A versão de um lado só (AQIDres e BQIDres) rebate a explicação alternativa de descoberta de preço via ordens limitadas. A medida pode ser calculada em intervalos de 5 minutos.

**Onde ela é frágil em large-tick:**

1. **Melhoria não é sinônimo de undercutting.** A eq. 1 conta toda melhoria, inclusive a recomposição de um nível que acabou de ser esvaziado. Com spread de 1 tick, quase toda melhoria é recomposição, porque não há espaço dentro do spread.
2. **Uma identidade contábil.** Se o spread começa e termina o dia em 1 tick e as variações são de um tick, o total de melhorias é igual ao total de deteriorações (por negócio e por cancelamento). O QID passa a ser função só da fração de deteriorações causadas por negócio. É uma medida de composição, não de competição.
3. **O argumento do artigo para ações restritas é específico dos EUA.** Ele se apoia na variação intradiária do spread e em rebates de maker (nota 11). Ambos precisam ser verificados no WIN.
4. **A janela de 10 ms resolve um problema que o MBO não tem.** Ela existe por latência do SIP e por clusters de negócios (seção 3.1). No MBO de um único mercado, a atribuição de uma deterioração a um negócio é exata.
5. **O controle de liquidez degenera.** Com spread quase constante em 1 tick, a variação diária do spread relativo vem quase toda do nível de preço. O controle absorve o tick relativo, não a liquidez. A variante com spread em reais (Apêndice C.1) degenera por completo.
6. **A padronização perde a função.** O desvio padrão do trimestre anterior serve para tornar ações comparáveis. Com um único instrumento, ela apenas reescala a série no tempo.
7. **A faixa mais apertada do artigo não é o WIN.** O grupo de spread abaixo de 3 centavos ainda passa parte do dia com spread acima de 1 tick; as corridas nessas ações começam em torno de 2 centavos (Figura 3). O WIN fica fora do suporte empírico do artigo.

## Mapeamento artigo → WIN

A implementação tem duas versões: **R (replicação)**, o mais fiel possível ao artigo, e **A (auditoria)**, que usa o que o MBO permite e isola o componente mecânico. As duas rodam sempre lado a lado.

| Elemento | Artigo | Versão R no WIN | Versão A no WIN |
| --- | --- | --- | --- |
| Fonte | Daily TAQ, NBBO consolidado, ms | Book reconstruído do MBO, melhor nível | Igual |
| Unidade | Ação-dia (e 5 min no intradiário) | Contrato-dia e 5 min | Igual |
| Instrumento | Ações com preço ≥ US$ 5 | Vencimento de maior volume do dia | Igual, com marcação da semana de rolagem |
| Horário | 9h45–15h45 (exclui 15 min das pontas) | Pregão contínuo, excluindo 15 min após a abertura e antes do call de fechamento, leilões e 1 min após cada leilão | Igual |
| Melhoria | Toda melhoria do NBB ou NBO | Toda melhoria do melhor bid ou ask | Separada em undercut e recomposição (seção de contagem) |
| Deterioração por negócio | Primeira deterioração em até 10 ms após um negócio | Igual, janela de 10 ms | Atribuição exata: o evento de execução que esvazia o nível |
| Unidade de atualização | Atualização de cotação no SIP | Estado do book ao fim de cada evento de casamento (mesmo número de sequência) | Igual |
| Spread de controle | ln do spread relativo ponderado pelo tempo | Igual | Igual, mais a fração do tempo com spread de 1 tick |
| Estimação | Ação-trimestre, parâmetros do trimestre anterior | Trimestre-calendário, parâmetros do trimestre anterior | Igual, e janela móvel de 60 pregões como robustez |
| Padronização | DP do QID do trimestre anterior | Igual | Igual |
| Sinal | × −1 | × −1 | × −1 |

**Fuso e horários.** Todos os timestamps em horário de Brasília. Os horários de abertura e fechamento do WIN mudam ao longo da amostra (horário de verão americano e ajustes da B3); usar a tabela de horários vigente em cada data, nunca horários fixos no código.

**Tick.** O tick do WIN é de 5 pontos. Todas as variações de preço são convertidas em ticks inteiros; uma variação não inteira indica erro na reconstrução e interrompe o pipeline.

## Contagem de eventos no MBO

A contagem é feita sobre a sequência de estados do melhor nível ao fim de cada evento de casamento; cada mudança de preço de um lado gera exatamente um evento classificado.

**Estado.** Para cada evento de casamento k (mensagens com o mesmo número de sequência agregadas), registrar t_k, b_k e a_k em ticks inteiros, q^b_k e q^a_k, e a lista de mensagens do evento (tipo: inclusão, cancelamento, modificação, execução; lado; preço; quantidade). Só entram estados com os dois lados não vazios e b_k < a_k.

**Definições por lado (bid; o ask é simétrico com sinais trocados):**

- **Melhoria:** b_k > b_{k−1}. Tamanho em ticks: b_k − b_{k−1}.
- **Deterioração:** b_k < b_{k−1}. Tamanho em ticks: b_{k−1} − b_k.
- **Deterioração por negócio, versão R:** a primeira deterioração do bid em até 10 ms após qualquer negócio. Variante R2: o negócio precisa ser iniciado por venda.
- **Deterioração por negócio, versão A:** o evento k contém execuções no preço b_{k−1} e a quantidade nesse preço chega a zero dentro do próprio evento.
- **Deterioração por cancelamento, versão A:** a quantidade em b_{k−1} chega a zero e a última mensagem que a reduziu foi um cancelamento ou uma modificação de preço.

**Classificação das melhorias (só versão A).** Para uma melhoria do bid para o preço p no instante t, com janela W:

1. **Recomposição:** p ≤ maior melhor bid observado em (t − W, t). O nível foi ocupado há pouco e está sendo refeito.
2. **Deslocamento:** não é recomposição, e o ask sofreu deterioração em (t − W, t). O preço inteiro está se movendo.
3. **Undercut:** nenhum dos dois casos. É a melhoria competitiva que o artigo quer medir.

W padrão = 1 s; sensibilidade com W ∈ {100 ms, 1 s, 5 s}.

**Casos de borda.**

- Uma agressão que varre vários níveis gera uma única deterioração, de vários ticks, porque a contagem é por evento e não por mensagem.
- Ordens iceberg que repõem quantidade sem mudar o preço não geram evento.
- Uma modificação que altera o preço é tratada como cancelamento seguido de inclusão.
- O primeiro estado após cada leilão serve apenas de referência: não gera melhoria nem deterioração.

## Agregação em QID

Cada período (dia ou bloco de 5 minutos) produz quatro QIDs: o do artigo (R), o com atribuição exata (A), o só de undercuts (U) e os de um lado só.

QID^R = (#Impr − #DeterTrade^10ms) / (#Impr + #DeterTrade^10ms)
QID^A = (#Impr − #DeterExec) / (#Impr + #DeterExec)
QID^U = (#Undercut − #DeterExec) / (#Undercut + #DeterExec)

As contagens somam bid e ask, como na Tabela 1 do artigo. As versões de um lado só (BQID e AQID, como na seção 6 do artigo) usam apenas as contagens do bid ou do ask. Um período com denominador zero tem QID ausente e é registrado.

Variáveis por período: n_impr, n_undercut, n_refill, n_shift; n_det_all, n_det_exec, n_det_cancel, n_det_trade10ms; share_1tick_impr, share_1tick_det_trade; spread_tw_ticks; pct_spread_tw; frac_1tick; volume, qvol; delta_spread_ticks.

**Blocos de 5 minutos.** Grade alinhada ao relógio a partir do início da sessão contínua válida. Um bloco que cruza um leilão é descartado. O estado inicial de cada bloco é o último estado do bloco anterior, sem contar eventos.

## Regressão QIDres

Passo 1 — estimação no trimestre q−1: QID_{q,t} = a_q + b_q ln(%Spread_{q,t}) + u.
Passo 2 — QIDres_{q,t} = −(QID_{q,t} − (â_{q−1} + b̂_{q−1} ln(%Spread_{q,t}))) / S(QID)_{q−1}.
S(QID) é o DP dos QIDs diários observados em q−1, não o dos resíduos. O primeiro trimestre só estima.

Rolagem: indicadora da semana de rolagem como diagnóstico; se significativa no passo 1, reestimar excluindo essas semanas.

Variantes: MC (ln spread em reais, qvol, volume), 1 ms, Frac1, Rolling (60 pregões), Winsor (1%/99%).

Intradiária (5 min): passo 1 com blocos de um dia de controle (5 pregões antes) e do próprio dia, com efeitos fixos de horário; passo 2 com essa estimativa.

## Diagnósticos de auditoria

ΔSpread_t = Σticks^deter − Σticks^impr; #Impr ≈ #Deter ⇒ QID ≈ (1−θ)/(1+θ), θ = #DeterExec/#Deter.

| ID | Diagnóstico | Como calcular | Referência no artigo | Sinal de alerta |
| --- | --- | --- | --- | --- |
| D1 | Identidade do spread | Resíduo da identidade acima por período | Não reportado | Qualquer resíduo diferente de zero: erro de contagem |
| D2 | QID como composição | R² da regressão de QID^R em (1−θ)/(1+θ), diário e 5 min | Não reportado | R² acima de 0,8 |
| D3 | Composição das melhorias | Frações de undercut, recomposição e deslocamento, por W | Não reportado | Undercut abaixo de 10% das melhorias |
| D4 | Restrição de tick | Distribuição de frac_1tick e de spread_tw_ticks | ~2 centavos (Fig. 3) | frac_1tick acima de 0,95 |
| D5 | Variações de um tick | share_1tick_impr e share_1tick_det_trade | 0,80 e 0,59 (Tabela 1, bid) | Valores próximos de 1 nas duas |
| D6 | Distribuição do QID | Média, mediana, % negativos | 0,61; 0,63; 0,05% (Tabela 1) | Média perto de zero ou muitos negativos |
| D7 | Atribuição R contra A | Concordância entre a janela de 10 ms e a atribuição exata | Mediana de 5 ms (seção 3.1) | Concordância abaixo de 90% |
| D8 | Primeiro estágio | R² e sinal de b por trimestre | R² 47,35% (Fig. E.1) | R² perto de zero ou b trocando de sinal |
| D9 | Momentos da QIDres | Média, DP, assimetria, AR(1) | 0,08; 1,54 (Tabela 1, Apêndice D) | DP muito acima de 1,5 ou AR(1) forte |
| D10 | Ortogonalidade | Correlação da QIDres com spread, volume e qvol | Próxima de zero (Tabela 1, Painel B) | |ρ| > 0,3 |
| D11 | Comparação R, A e U | Correlação entre QIDres^R, ^A e ^U | Não se aplica | R e U pouco correlacionadas |

## Testes unitários com books sintéticos

1. Corrida limpa: spread 5 ticks; 3 melhorias de 1 tick no bid; venda esvazia o melhor bid → 3 undercuts, 1 deterioração por execução, QID = 0,5.
2. Recomposição: spread 1 tick; venda esvazia o bid; nova ordem volta ao mesmo preço 200 ms depois → 1 det. exec, 1 recomposição, 0 undercuts.
3. Deslocamento: spread 1 tick; compra esvazia o ask; bid sobe 1 tick 300 ms depois → 1 det. ask exec, 1 melhoria bid deslocamento.
4. Varredura: agressão consome 3 níveis do ask num único evento → 1 deterioração de 3 ticks.
5. Cancelamento: único lote do melhor bid cancelado → 1 det. cancelamento; R por negócio só se houver negócio nos 10 ms anteriores.
6. Identidade: em qualquer sequência sintética aleatória, resíduo D1 = 0.
7. Leilão: leilão no meio da sequência não gera eventos; primeiro estado posterior só referência.
8. QIDres: QIDs sintéticos a + b ln(spread) + ruído → regressão recupera a, b; QIDres com média ≈ 0 e DP ≈ 1.

Ordem de execução: (1) testes; (2) um pregão + inspeção de 20 eventos por classe; (3) amostra inteira + D1; (4) D2–D7; (5) regressões + D8–D11; (6) comparar com H1, H2, P2a.

Resultado esperado registrado antes de rodar: se a hipótese da tese estiver certa, D2 mostra R² alto, D3 mostra poucos undercuts e D11 mostra QIDres^R e QIDres^U pouco correlacionadas.
