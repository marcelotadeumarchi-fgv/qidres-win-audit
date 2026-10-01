#!/bin/bash
cd "$(dirname "$0")/../.."
PY="${PY:-python}"
LOG=auditoria_qidres/saida/rodada3/execucao.log
mkdir -p auditoria_qidres/saida/rodada3
run() { echo "$(date '+%F %T') $1" >> $LOG; $PY -m auditoria_qidres.$2 >> $LOG 2>&1 || { echo "$(date '+%F %T') PAROU em $2" >> $LOG; exit 1; }; }
run "extração de negócios" rodada3_negocios
echo "$(date '+%F %T') FIM extração" >> $LOG
