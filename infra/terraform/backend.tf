# State remoto no S3 (criptografado, versionado, trava por lockfile no próprio bucket).
# Configuração parcial: os valores de cada região ficam em envs/<regiao>.backend.hcl.
#   terraform init -backend-config=envs/sa-east-1.backend.hcl
terraform {
  backend "s3" {}
}
