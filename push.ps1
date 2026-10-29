<#
.SYNOPSIS
  Build e push das 4 imagens para o ECR, com a tag do commit atual.
.EXAMPLE
  .\push.ps1 us-east-2 123456789012
  $env:PLATFORM = "linux/arm64"; .\push.ps1 us-east-2 123456789012   # padrão: linux/amd64
.NOTES
  Requer: aws cli v2 autenticado (aws sso login / $env:AWS_PROFILE), Docker Desktop com buildx, git.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Regiao,
    [Parameter(Mandatory = $true, Position = 1)][string]$Conta
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Invoke-Checked {
    param([scriptblock]$Comando, [string]$Descricao)
    & $Comando
    if ($LASTEXITCODE -ne 0) { throw "Falhou: $Descricao (código $LASTEXITCODE)" }
}

# Executa um comando nativo só para saber se deu certo (sem lançar exceção no PowerShell 5.1).
function Test-Native {
    param([scriptblock]$Comando)
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Comando *> $null; return ($LASTEXITCODE -eq 0) }
    finally { $ErrorActionPreference = $anterior }
}

$Platform = if ($env:PLATFORM) { $env:PLATFORM } else { "linux/amd64" }
$Tag = (git rev-parse --short HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Não foi possível obter o commit atual (é um repositório git?)" }
if (git status --porcelain) {
    Write-Warning "Há alterações não commitadas; a imagem com a tag $Tag não corresponde exatamente ao commit."
}
$Registry = "$Conta.dkr.ecr.$Regiao.amazonaws.com"

$Imagens = @(
    @{ Nome = "chat-api";     Contexto = "services/chat-api";     Target = "runtime" },
    @{ Nome = "commerce-api"; Contexto = "services/commerce-api"; Target = "runtime" },
    @{ Nome = "order-worker"; Contexto = "services/order-worker"; Target = "runtime" },
    @{ Nome = "db-migrate";   Contexto = "db";                    Target = "" }
)

Write-Host ">> login no ECR $Registry"
$senha = aws ecr get-login-password --region $Regiao
if ($LASTEXITCODE -ne 0) { throw "Falhou: aws ecr get-login-password" }
$senha | docker login --username AWS --password-stdin $Registry
if ($LASTEXITCODE -ne 0) { throw "Falhou: docker login" }

foreach ($img in $Imagens) {
    $Repo = "setup-pronto/$($img.Nome)"
    $Imagem = "$Registry/${Repo}:$Tag"

    if (-not (Test-Native { aws ecr describe-repositories --region $Regiao --repository-names $Repo })) {
        Write-Host ">> criando repositório $Repo"
        Invoke-Checked { aws ecr create-repository --region $Regiao --repository-name $Repo `
            --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE | Out-Null } "create-repository $Repo"
    }

    if (Test-Native { aws ecr describe-images --region $Regiao --repository-name $Repo --image-ids "imageTag=$Tag" }) {
        Write-Host ">> $Imagem já existe no ECR (tags imutáveis); pulando"
        continue
    }

    Write-Host ">> build $Imagem ($Platform)"
    $buildArgs = @("buildx", "build", "--platform", $Platform, "-t", $Imagem, "--push")
    if ($img.Target) { $buildArgs += @("--target", $img.Target) }
    $buildArgs += $img.Contexto
    Invoke-Checked { docker @buildArgs } "build $Imagem"
}

Write-Host ""
Write-Host "Imagens publicadas com a tag ${Tag}:"
foreach ($img in $Imagens) { Write-Host "  $Registry/setup-pronto/$($img.Nome):$Tag" }
