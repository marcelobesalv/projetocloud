#!/usr/bin/env bash
# Dispara o CodeBuild que constrói e publica as imagens no ECR e espera o fim; sai com erro se o build falhar.
# Chamado pelo terraform_data.imagens (build.tf). Uso: build-imagens.sh <regiao> <projeto-codebuild> <tag>
#
# Logo depois de criar a role do CodeBuild, o IAM ainda pode não ter propagado a política: o build falha na fila
# ("not authorized"/"does not allow") antes de rodar qualquer comando. Esse caso é repetido, com espera; qualquer outra
# falha encerra o apply com o final do log.
set -euo pipefail
REGION=$1 PROJETO=$2 TAG=$3
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION

for tentativa in 1 2 3 4 5 6; do
  ID=$(aws codebuild start-build --project-name "$PROJETO" \
    --environment-variables-override "name=TAG,value=$TAG,type=PLAINTEXT" --query 'build.id' --output text)
  echo "build das imagens ($TAG), tentativa $tentativa: $ID"
  while true; do
    sleep 15
    read -r STATUS FASE < <(aws codebuild batch-get-builds --ids "$ID" --query 'builds[0].[buildStatus,currentPhase]' --output text)
    echo "  $(date -u +%T) $STATUS $FASE"
    [ "$STATUS" = IN_PROGRESS ] || break
  done
  [ "$STATUS" = SUCCEEDED ] && break

  MOTIVO=$(aws codebuild batch-get-builds --ids "$ID" --output text --query \
    'builds[0].phases[?phaseStatus!=`SUCCEEDED` && phaseStatus!=null] | [0].[phaseType, contexts[0].message]')
  echo "  falhou: $MOTIVO"
  if [[ "$MOTIVO" =~ ^(QUEUED|PROVISIONING) ]] && [[ "$MOTIVO" =~ (not\ authorized|does\ not\ allow) ]] && [ $tentativa -lt 6 ]; then
    echo "  permissão do IAM ainda propagando; nova tentativa em 20 s"
    sleep 20
    continue
  fi
  GRUPO=$(aws codebuild batch-get-builds --ids "$ID" --query 'builds[0].logs.groupName' --output text)
  STREAM=$(aws codebuild batch-get-builds --ids "$ID" --query 'builds[0].logs.streamName' --output text)
  if [ "$STREAM" != None ]; then
    echo "final do log:"
    aws logs get-log-events --log-group-name "$GRUPO" --log-stream-name "$STREAM" --limit 40 \
      --query 'events[].message' --output text | tr '\t' '\n' | tail -n 40 || true
  fi
  exit 1
done

GRUPO=$(aws codebuild batch-get-builds --ids "$ID" --query 'builds[0].logs.groupName' --output text)
STREAM=$(aws codebuild batch-get-builds --ids "$ID" --query 'builds[0].logs.streamName' --output text)
aws logs filter-log-events --log-group-name "$GRUPO" --log-stream-names "$STREAM" --filter-pattern '">>"' \
  --query 'events[].message' --output text | tr '\t' '\n' | grep -E '^>> ' || true
