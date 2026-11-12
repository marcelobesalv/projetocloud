#!/usr/bin/env bash
# DLQ de pedidos: inspecionar e reprocessar.
#   ver          mostra até 10 mensagens (corpo, quantas vezes foram recebidas) sem tirá-las da DLQ
#   reprocessar  devolve todas para a fila orders (redrive nativo do SQS, StartMessageMoveTask) e acompanha
# Uso: ops/dlq.sh [regiao] [ver|reprocessar]
#
# Reprocessar é seguro: o worker trava o pedido e ignora o que não está PENDENTE, então pedidos já
# resolvidos (inclusive pelo reconciliador do commerce-api, que reenvia pedidos parados) só são apagados.
# Corrija a causa antes: mensagem inválida ou pedido inexistente volta para a DLQ depois de 5 recebimentos.
set -euo pipefail
source "$(dirname "$0")/comum.sh" "${1:-}"
ACAO=${2:-ver}
arn(){ aws sqs get-queue-attributes --queue-url "$1" --attribute-names QueueArn --query Attributes.QueueArn --output text; }
qtd(){ aws sqs get-queue-attributes --queue-url "$1" --attribute-names ApproximateNumberOfMessages --query Attributes.ApproximateNumberOfMessages --output text; }

echo "== DLQ: $(qtd "$DLQ_URL") mensagem(ns) · fila orders: $(qtd "$Q_URL")"
case "$ACAO" in
  ver)
    # visibility-timeout 0: as mensagens continuam visíveis na DLQ (a DLQ não tem redrive, nada se perde)
    aws sqs receive-message --queue-url "$DLQ_URL" --max-number-of-messages 10 --visibility-timeout 0 \
      --attribute-names ApproximateReceiveCount SentTimestamp --message-attribute-names All \
      --query 'Messages[].{id:MessageId,recebida:Attributes.ApproximateReceiveCount,enviada:Attributes.SentTimestamp,corpo:Body}' \
      --output table
    ;;
  reprocessar)
    aws sqs start-message-move-task --source-arn "$(arn "$DLQ_URL")" --destination-arn "$(arn "$Q_URL")" \
      --max-number-of-messages-per-second 10 --query TaskHandle --output text >/dev/null
    echo "== $(date -u +%T) redrive iniciado"
    for i in $(seq 40); do
      sleep 3
      read -r ST MOV TOT < <(aws sqs list-message-move-tasks --source-arn "$(arn "$DLQ_URL")" --max-results 1 \
        --query 'Results[0].[Status,ApproximateNumberOfMessagesMoved,ApproximateNumberOfMessagesToMove]' --output text)
      echo "   $ST: $MOV de $TOT movidas"
      [[ "$ST" == RUNNING ]] || break
    done
    echo "== DLQ: $(qtd "$DLQ_URL") · fila orders: $(qtd "$Q_URL")  (acompanhe: logs do order-worker, event=order_processed)"
    ;;
  *) echo "ação desconhecida: $ACAO (use ver ou reprocessar)"; exit 1 ;;
esac
