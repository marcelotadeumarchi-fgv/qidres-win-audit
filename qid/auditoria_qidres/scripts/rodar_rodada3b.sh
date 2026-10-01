#!/bin/bash
cd "$(dirname "$0")/../.."
LOG=auditoria_qidres/saida/rodada3/execucao_3b.log
echo "$(date '+%F %T') início rodada 3b (E2, E3)" >> $LOG
"${PY:-python}" -m auditoria_qidres.rodada3b_robustez >> $LOG 2>&1 && echo "$(date '+%F %T') FIM 3b" >> $LOG || echo "$(date '+%F %T') PAROU 3b" >> $LOG
