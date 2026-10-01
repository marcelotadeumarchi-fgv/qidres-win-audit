#!/bin/bash
cd "$(dirname "$0")/../.."
LOG=auditoria_qidres/saida/rodada3/execucao_3c.log
echo "$(date '+%F %T') início E4" >> $LOG
"${PY:-python}" -m auditoria_qidres.rodada3c_theta >> $LOG 2>&1 && echo "$(date '+%F %T') FIM E4" >> $LOG || echo "$(date '+%F %T') PAROU E4" >> $LOG
