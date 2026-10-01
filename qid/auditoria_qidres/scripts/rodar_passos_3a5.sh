#!/bin/bash
# Executor desacoplado da sessão: espera o passo 3 em andamento, depois roda 3 (retomável), 4 e 5.
# Para no passo 3 se D1 falhar (código != 0). Log: auditoria_qidres/saida/execucao_3a5.log
cd "$(dirname "$0")/../.."
PY="${PY:-python}"
LOG=auditoria_qidres/saida/execucao_3a5.log
echo "$(date '+%F %T') início; aguardando PID $1 (passo 3 em andamento)" >> $LOG
while [ -n "$1" ] && kill -0 "$1" 2>/dev/null; do sleep 30; done
echo "$(date '+%F %T') passo 3 (retomável)" >> $LOG
$PY -m auditoria_qidres.passo3_amostra >> $LOG 2>&1 || { echo "$(date '+%F %T') PAROU no passo 3 (D1 ou erro)" >> $LOG; exit 1; }
echo "$(date '+%F %T') passo 4" >> $LOG
$PY -m auditoria_qidres.passo4_diagnosticos >> $LOG 2>&1 || { echo "$(date '+%F %T') PAROU no passo 4" >> $LOG; exit 1; }
echo "$(date '+%F %T') passo 5" >> $LOG
$PY -m auditoria_qidres.passo5_qidres >> $LOG 2>&1 || { echo "$(date '+%F %T') PAROU no passo 5" >> $LOG; exit 1; }
echo "$(date '+%F %T') FIM (passos 3–5 concluídos)" >> $LOG
