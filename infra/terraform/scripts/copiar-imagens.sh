#!/usr/bin/env bash
# Copia as 4 imagens de uma região para outra (mesma conta), sem rebuild.
# Uso: copiar-imagens.sh <regiao-origem> <regiao-destino> <tag>   ex.: copiar-imagens.sh us-east-2 sa-east-1 37791f6
# Requer docker (o CloudShell tem). Alternativa: ./push.sh <regiao-destino> <conta> na máquina de build.
set -euo pipefail
ORIG=$1 DEST=$2 TAG=$3
CONTA=$(aws sts get-caller-identity --query Account --output text)
R_ORIG=$CONTA.dkr.ecr.$ORIG.amazonaws.com
R_DEST=$CONTA.dkr.ecr.$DEST.amazonaws.com
aws ecr get-login-password --region "$ORIG" | docker login -u AWS --password-stdin "$R_ORIG"
aws ecr get-login-password --region "$DEST" | docker login -u AWS --password-stdin "$R_DEST"
for n in chat-api commerce-api order-worker db-migrate; do
  docker pull --platform linux/amd64 "$R_ORIG/setup-pronto/$n:$TAG"
  docker tag "$R_ORIG/setup-pronto/$n:$TAG" "$R_DEST/setup-pronto/$n:$TAG"
  docker push "$R_DEST/setup-pronto/$n:$TAG"
  docker rmi "$R_ORIG/setup-pronto/$n:$TAG" "$R_DEST/setup-pronto/$n:$TAG" >/dev/null
done
echo "imagens $TAG copiadas para $DEST"
