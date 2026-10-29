#!/usr/bin/env bash
# Build e push das 4 imagens para o ECR, com a tag do commit atual.
#   uso: ./push.sh <regiao> <conta>          ex.: ./push.sh us-east-2 123456789012
#   PLATFORM=linux/arm64 ./push.sh ...         (padrão linux/amd64, para instâncias EC2 x86)
# Requer: aws cli v2 autenticado (ex.: aws sso login / AWS_PROFILE), docker com buildx, git.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "uso: $0 <regiao> <conta>" >&2
  exit 1
fi
REGIAO="$1"
CONTA="$2"
PLATFORM="${PLATFORM:-linux/amd64}"
cd "$(dirname "$0")"

TAG="$(git rev-parse --short HEAD)"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "AVISO: há alterações não commitadas; a imagem com a tag $TAG não corresponde exatamente ao commit." >&2
fi
REGISTRY="${CONTA}.dkr.ecr.${REGIAO}.amazonaws.com"

# nome-da-imagem|contexto|target (vazio = estágio final do Dockerfile)
IMAGENS=(
  "chat-api|services/chat-api|runtime"
  "commerce-api|services/commerce-api|runtime"
  "order-worker|services/order-worker|runtime"
  "db-migrate|db|"
)

echo ">> login no ECR $REGISTRY"
aws ecr get-login-password --region "$REGIAO" | docker login --username AWS --password-stdin "$REGISTRY"

for item in "${IMAGENS[@]}"; do
  IFS='|' read -r NOME CONTEXTO TARGET <<<"$item"
  REPO="setup-pronto/${NOME}"
  IMAGEM="${REGISTRY}/${REPO}:${TAG}"

  if ! aws ecr describe-repositories --region "$REGIAO" --repository-names "$REPO" >/dev/null 2>&1; then
    echo ">> criando repositório $REPO"
    aws ecr create-repository --region "$REGIAO" --repository-name "$REPO" \
      --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE >/dev/null
  fi

  if aws ecr describe-images --region "$REGIAO" --repository-name "$REPO" --image-ids imageTag="$TAG" >/dev/null 2>&1; then
    echo ">> $IMAGEM já existe no ECR (tags imutáveis); pulando"
    continue
  fi

  echo ">> build $IMAGEM ($PLATFORM)"
  ARGS=(--platform "$PLATFORM" -t "$IMAGEM" --push)
  [[ -n "$TARGET" ]] && ARGS+=(--target "$TARGET")
  docker buildx build "${ARGS[@]}" "$CONTEXTO"
done

echo
echo "Imagens publicadas com a tag $TAG:"
for item in "${IMAGENS[@]}"; do
  echo "  ${REGISTRY}/setup-pronto/${item%%|*}:${TAG}"
done
