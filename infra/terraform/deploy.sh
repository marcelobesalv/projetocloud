#!/usr/bin/env bash
# Cria (ou atualiza, ou remove) o ambiente completo da Setup Pronto com um único comando, sem nenhum passo manual:
#   1) bucket do state remoto (módulo bootstrap/), se ainda não existir na conta/região;
#   2) terraform init com o backend apontando para esse bucket (nome derivado da conta, nada a editar);
#   3) terraform apply -auto-approve: rede, ECS/EC2, ALB, RDS, SQS, IAM, observabilidade, auditoria, build das
#      imagens no CodeBuild, migração do banco e os 3 serviços.
#
# Uso (da pasta infra/terraform, com credenciais da AWS no ambiente: CloudShell, AWS_PROFILE ou variáveis):
#   ./deploy.sh                      # sa-east-1
#   ./deploy.sh us-east-1            # outra região
#   ./deploy.sh sa-east-1 plan       # só mostra o que mudaria
#   ./deploy.sh sa-east-1 destroy    # remove tudo (o bucket do state fica)
# Argumentos depois da ação vão direto para o terraform (ex.: ./deploy.sh sa-east-1 apply -var alarm_email=a@b.com).
set -euo pipefail
cd "$(dirname "$0")"

REGION=${1:-sa-east-1}
ACAO=${2:-apply}
shift $(( $# >= 2 ? 2 : $# ))
PROJETO=setup-pronto
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION  # no CloudShell AWS_REGION já vem definido e tem precedência

case "$ACAO" in apply|plan|destroy) ;; *) echo "ação inválida: $ACAO (use apply, plan ou destroy)" >&2; exit 2 ;; esac

# --- pré-requisitos -------------------------------------------------------------------------------------------
falta=0
for cmd in terraform aws python3; do
  command -v "$cmd" >/dev/null || { echo "falta o comando '$cmd' (veja os pré-requisitos no README)" >&2; falta=1; }
done
[ $falta = 0 ] || exit 1
TF_VER=$(terraform version -json | python3 -c 'import json,sys; print(json.load(sys.stdin)["terraform_version"])')
python3 - "$TF_VER" <<'PY' || { echo "Terraform $TF_VER: é preciso >= 1.11 (argumentos write-only e lockfile no S3)" >&2; exit 1; }
import sys
maior, menor = (int(x) for x in sys.argv[1].split(".")[:2])
sys.exit(0 if (maior, menor) >= (1, 11) else 1)
PY

CONTA=$(aws sts get-caller-identity --query Account --output text) \
  || { echo "sem credenciais da AWS válidas (aws sso login / CloudShell)" >&2; exit 1; }
BUCKET="$PROJETO-tfstate-$CONTA-$REGION"
echo "==> conta $CONTA, região $REGION, ação $ACAO, Terraform $TF_VER"

# --- 1) bucket do state ---------------------------------------------------------------------------------------
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "==> bucket do state já existe: $BUCKET"
elif [ "$ACAO" = destroy ]; then
  echo "bucket do state $BUCKET não existe: não há ambiente para remover" >&2; exit 1
else
  echo "==> criando o bucket do state: $BUCKET"
  # O bootstrap usa state local próprio; com TF_DATA_DIR definido, ele ganha um diretório separado do principal.
  ( cd bootstrap
    [ -z "${TF_DATA_DIR:-}" ] || export TF_DATA_DIR="$TF_DATA_DIR-bootstrap"
    terraform init -input=false
    terraform apply -input=false -auto-approve -var "region=$REGION" -var "project=$PROJETO" )
fi

# --- 2) init com o backend da conta/região --------------------------------------------------------------------
terraform init -input=false -reconfigure \
  -backend-config="bucket=$BUCKET" \
  -backend-config="key=$PROJETO/$REGION/terraform.tfstate" \
  -backend-config="region=$REGION" \
  -backend-config="encrypt=true" \
  -backend-config="use_lockfile=true"

# --- 3) plan / apply / destroy --------------------------------------------------------------------------------
VARS=(-var "region=$REGION")
[ -f "envs/$REGION.tfvars" ] && VARS=(-var-file "envs/$REGION.tfvars")
case "$ACAO" in
  plan)    terraform plan -input=false "${VARS[@]}" "$@" ;;
  destroy) terraform destroy -input=false -auto-approve "${VARS[@]}" "$@" ;;
  apply)
    terraform apply -input=false -auto-approve "${VARS[@]}" "$@"
    echo
    echo "==> pronto. Endereço da aplicação: $(terraform output -raw url)"
    ;;
esac
