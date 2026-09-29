[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Message,

    [string]$Config = (Join-Path $PSScriptRoot '..\deploy.config.psd1'),

    [switch]$SkipTests,
    [switch]$SkipCommit,
    [switch]$SkipDeploy
)

$ErrorActionPreference = 'Stop'

function Invoke-Native {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Comando falhou ($LASTEXITCODE): $File $($Arguments -join ' ')"
    }
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $prefix = "$Name="
    $line = Get-Content -LiteralPath $Path | Where-Object { $_.StartsWith($prefix) } | Select-Object -First 1
    if ($null -eq $line) { return $null }
    return $line.Substring($prefix.Length).Trim().Trim('"').Trim("'")
}

function Invoke-PortainerApi {
    param(
        [ValidateSet('GET', 'POST', 'PUT')][string]$Method,
        [string]$Uri,
        [string]$ApiKey,
        [object]$Body
    )
    $params = @{
        Method      = $Method
        Uri         = $Uri
        Headers     = @{ 'X-API-Key' = $ApiKey }
        UseBasicParsing = $true
    }
    if ($null -ne $Body) {
        $params.ContentType = 'application/json'
        $params.Body = $Body | ConvertTo-Json -Depth 20 -Compress
    }
    $response = Invoke-WebRequest @params
    if ([string]::IsNullOrWhiteSpace($response.Content)) { return $null }
    return $response.Content | ConvertFrom-Json
}

function Wait-GitHubActions {
    param([hashtable]$Settings, [string]$Commit)
    if (-not $Settings.Enabled) { return }

    $deadline = (Get-Date).AddSeconds([int]$Settings.TimeoutSeconds)
    $headers = @{ Accept = 'application/vnd.github+json' }
    $token = [Environment]::GetEnvironmentVariable([string]$Settings.TokenEnvironmentVariable)
    if ($token) { $headers.Authorization = "Bearer $token" }
    $encodedWorkflow = [Uri]::EscapeDataString([string]$Settings.Workflow)
    $uri = "https://api.github.com/repos/$($Settings.Repository)/actions/workflows/$encodedWorkflow/runs?head_sha=$Commit&per_page=10"

    Write-Host 'Aguardando o GitHub Actions publicar a imagem...'
    while ((Get-Date) -lt $deadline) {
        try {
            $result = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers
            $run = $result.workflow_runs | Sort-Object created_at -Descending | Select-Object -First 1
            if ($run.status -eq 'completed') {
                if ($run.conclusion -ne 'success') {
                    throw "Pipeline terminou com status '$($run.conclusion)': $($run.html_url)"
                }
                Write-Host "Pipeline concluído: $($run.html_url)"
                return
            }
        } catch {
            if ($_.Exception.Message -like 'Pipeline terminou*') { throw }
            Write-Verbose "Ainda nao foi possivel consultar o workflow: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 10
    }
    throw "Tempo esgotado aguardando o GitHub Actions para o commit $Commit."
}

function Wait-HealthCheck {
    param([string]$Uri, [int]$TimeoutSeconds)
    if ([string]::IsNullOrWhiteSpace($Uri)) { return }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Uri $Uri
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Host "Saude confirmada: $Uri (HTTP $($response.StatusCode))"
                return
            }
        } catch {
            Write-Verbose "Servico ainda indisponivel: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    throw "O serviço não ficou saudável em $TimeoutSeconds segundos: $Uri"
}

$configPath = (Resolve-Path -LiteralPath $Config).Path
$settings = Import-PowerShellDataFile -LiteralPath $configPath
$configDirectory = Split-Path -Parent $configPath
$repositoryPath = [IO.Path]::GetFullPath((Join-Path $configDirectory $settings.RepositoryPath))
Push-Location $repositoryPath
try {
    Invoke-Native git @('rev-parse', '--is-inside-work-tree')
    $branch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne $settings.Branch) {
        throw "Branch atual '$branch'; esperada '$($settings.Branch)'."
    }

    if (-not $SkipTests) {
        foreach ($testCommand in $settings.TestCommands) {
            Write-Host "Executando teste: $testCommand"
            & ([scriptblock]::Create($testCommand))
            if ($LASTEXITCODE -ne 0) { throw "Teste falhou: $testCommand" }
        }
    }

    if (-not $SkipCommit) {
        Invoke-Native git @('add', '--all')
        $staged = & git diff --cached --name-only
        if ($LASTEXITCODE -ne 0) { throw 'Não foi possível inspecionar os arquivos preparados.' }
        $blocked = @($staged | Where-Object { $_ -match '(^|/)(\.env($|\.)|.*\.(pfx|p12|pem|key)$)' })
        if ($blocked.Count -gt 0) {
            & git reset -- @blocked
            if ($LASTEXITCODE -ne 0) { throw 'Falha ao remover arquivos sensiveis do stage.' }
            throw "Arquivos sensiveis foram removidos do stage: $($blocked -join ', ')"
        }
        if ($staged.Count -gt 0) {
            Invoke-Native git @('commit', '-m', $Message)
        } else {
            Write-Host 'Nenhuma alteracao versionavel; usando o commit atual.'
        }
        Invoke-Native git @('push', $settings.Remote, $settings.Branch)
    }

    $commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel obter o commit atual.' }

    if (-not $SkipDeploy) {
        Wait-GitHubActions -Settings $settings.GitHubActions -Commit $commit

        $portainer = $settings.Portainer
        $apiKey = [Environment]::GetEnvironmentVariable([string]$portainer.ApiKeyEnvironmentVariable)
        if (-not $apiKey -and $portainer.ApiKeyDotEnvPath) {
            $dotenv = [IO.Path]::GetFullPath((Join-Path $repositoryPath $portainer.ApiKeyDotEnvPath))
            $apiKey = Get-DotEnvValue -Path $dotenv -Name $portainer.ApiKeyEnvironmentVariable
        }
        if (-not $apiKey) {
            throw "Defina $($portainer.ApiKeyEnvironmentVariable) no ambiente ou no .env ignorado pelo Git."
        }

        $base = ([string]$portainer.Url).TrimEnd('/')
        $stackUri = "$base/api/stacks/$($portainer.StackId)"
        $stack = Invoke-PortainerApi -Method GET -Uri $stackUri -ApiKey $apiKey
        if ([int]$stack.EndpointId -ne [int]$portainer.EndpointId) {
            throw "A stack esta no endpoint $($stack.EndpointId), nao no endpoint configurado $($portainer.EndpointId)."
        }
        $stackFile = Invoke-PortainerApi -Method GET -Uri "$stackUri/file" -ApiKey $apiKey
        $image = ([string]$portainer.ImageTemplate).Replace('{commit}', $commit)
        $imageVariable = $stack.Env | Where-Object { $_.name -eq $portainer.ImageEnvironmentVariable } | Select-Object -First 1
        if (-not $imageVariable) {
            throw "Variavel '$($portainer.ImageEnvironmentVariable)' nao encontrada na stack."
        }
        $imageVariable.value = $image
        $payload = @{
            env              = @($stack.Env)
            prune            = $true
            pullImage        = $true
            stackFileContent = $stackFile.StackFileContent
        }
        if ($PSCmdlet.ShouldProcess($stack.Name, "publicar $image")) {
            Invoke-PortainerApi -Method PUT -Uri "${stackUri}?endpointId=$($portainer.EndpointId)" -ApiKey $apiKey -Body $payload | Out-Null
            Write-Host "Stack '$($stack.Name)' atualizada para $image"
        }
        Wait-HealthCheck -Uri $settings.HealthCheck.Url -TimeoutSeconds ([int]$settings.HealthCheck.TimeoutSeconds)
    }

    Write-Host "Concluido no commit $commit"
} finally {
    Pop-Location
}
