#!/bin/bash
cd "$(dirname "$0")/../.."
PY="${PY:-python}"
LOG=auditoria_qidres/saida/rodada3/execucao.log
until grep -q -E "FIM extração|PAROU" $LOG; do sleep 20; done
grep -q PAROU $LOG && exit 1
echo "$(date '+%F %T') análise passos 1–3" >> $LOG
$PY -m auditoria_qidres.rodada3_viabilidade >> $LOG 2>&1 || { echo "$(date '+%F %T') PAROU na análise" >> $LOG; exit 1; }
echo "$(date '+%F %T') FIM análise" >> $LOG
