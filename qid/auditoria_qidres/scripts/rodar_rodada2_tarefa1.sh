#!/bin/bash
# Rodada 2, Tarefa 1: auditoria inteira (passos 3–6) nos 12 pregões + relatório lado a lado com o piloto.
cd "$(dirname "$0")/../.."
PY="${PY:-python}"
LOG=auditoria_qidres/saida/execucao_rodada2.log
run() { echo "$(date '+%F %T') $1" >> $LOG; $PY -m auditoria_qidres.$2 >> $LOG 2>&1 || { echo "$(date '+%F %T') PAROU em $2" >> $LOG; exit 1; }; }
echo "$(date '+%F %T') início rodada 2 / tarefa 1" >> $LOG
run "passo 3" passo3_amostra
run "passo 4" passo4_diagnosticos
run "passo 5" passo5_qidres
run "passo 6" passo6_comparacao
run "tarefa 1 (relatório)" tarefa1_relatorio
echo "$(date '+%F %T') FIM rodada 2 / tarefa 1" >> $LOG
