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
        [object]$Body,
        [int]$TimeoutSeconds = 30
    )
    $params = @{
        Method      = $Method
        Uri         = $Uri
        Headers     = @{ 'X-API-Key' = $ApiKey }
        UseBasicParsing = $true
        TimeoutSec  = $TimeoutSeconds
    }
    if ($null -ne $Body) {
        $params.ContentType = 'application/json'
        $params.Body = $Body | ConvertTo-Json -Depth 20 -Compress
    }
    $response = Invoke-WebRequest @params
    if ([string]::IsNullOrWhiteSpace($response.Content)) { return $null }
    return $response.Content | ConvertFrom-Json
}

function Invoke-PortainerImagePull {
    param(
        [hashtable]$Settings,
        [string]$ApiKey,
        [string]$Image
    )
    $portainer = $Settings.Portainer
    $registryId = [int]$portainer.RegistryId
    if ($registryId -le 0) {
        throw 'Portainer.RegistryId deve identificar o registro privado usado pela imagem.'
    }
    $separator = $Image.LastIndexOf(':')
    if ($separator -le $Image.LastIndexOf('/')) {
        throw "A imagem precisa conter uma tag explicita: $Image"
    }
    $repository = $Image.Substring(0, $separator)
    $tag = $Image.Substring($separator + 1)
    $registryAuth = [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes((@{ registryId = $registryId } | ConvertTo-Json -Compress))
    )
    $base = ([string]$portainer.Url).TrimEnd('/')
    $dockerBase = "$base/api/endpoints/$($portainer.EndpointId)/docker"
    $uri = "$dockerBase/images/create?fromImage=$([Uri]::EscapeDataString($repository))&tag=$([Uri]::EscapeDataString($tag))"
    Write-Host "Baixando imagem pelo Portainer: $Image"
    $response = Invoke-WebRequest -Method Post -Uri $uri -Headers @{
        'X-API-Key' = $ApiKey
        'X-Registry-Auth' = $registryAuth
    } -UseBasicParsing -TimeoutSec ([int]$portainer.PullTimeoutSeconds)
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300 -or $response.Content -match '"error(?:Detail)?"') {
        throw "Falha ao baixar a imagem '$Image' pelo Portainer."
    }
    Write-Host "Imagem disponivel no host: $Image"
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
    $nextNotice = Get-Date
    while ((Get-Date) -lt $deadline) {
        $statusMessage = 'workflow ainda nao localizado'
        try {
            $result = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -TimeoutSec 15
            $run = $result.workflow_runs | Sort-Object created_at -Descending | Select-Object -First 1
            if ($run) { $statusMessage = "status '$($run.status)'" }
            if ($run.status -eq 'completed') {
                if ($run.conclusion -ne 'success') {
                    throw "Pipeline terminou com status '$($run.conclusion)': $($run.html_url)"
                }
                Write-Host "Pipeline concluido: $($run.html_url)"
                return
            }
        } catch {
            if ($_.Exception.Message -like 'Pipeline terminou*') { throw }
            $statusMessage = "consulta indisponivel: $($_.Exception.Message)"
            Write-Verbose "Ainda nao foi possivel consultar o workflow: $($_.Exception.Message)"
        }
        if ((Get-Date) -ge $nextNotice) {
            Write-Host "GitHub Actions: $statusMessage. Nova consulta em 10 segundos."
            $nextNotice = (Get-Date).AddSeconds(30)
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
    throw "O servico nao ficou saudavel em $TimeoutSeconds segundos: $Uri"
}

function Update-PortainerContainer {
    param(
        [hashtable]$Settings,
        [string]$ApiKey,
        [string]$Image
    )
    $portainer = $Settings.Portainer
    $base = ([string]$portainer.Url).TrimEnd('/')
    $dockerBase = "$base/api/endpoints/$($portainer.EndpointId)/docker"
    $name = [string]$portainer.ContainerName
    $encodedName = [Uri]::EscapeDataString($name)
    $current = Invoke-PortainerApi -Method GET -Uri "$dockerBase/containers/$encodedName/json" -ApiKey $ApiKey
    if ([string]$current.Config.Image -eq $Image -and $current.State.Running) {
        Write-Host "Container '$name' ja utiliza $Image"
        return
    }

    $mounts = @($current.Mounts | ForEach-Object {
        $source = if ($_.Type -eq 'volume') { $_.Name } else { $_.Source }
        $mount = @{ Type = $_.Type; Source = $source; Target = $_.Destination; ReadOnly = -not $_.RW }
        if ($_.Type -eq 'bind') { $mount.BindOptions = @{ Propagation = $_.Propagation } }
        $mount
    })
    $labels = @{}
    $current.Config.Labels.psobject.Properties | ForEach-Object { $labels[$_.Name] = $_.Value }
    $payload = @{
        Image        = $Image
        Env          = @($current.Config.Env)
        Cmd          = @($current.Config.Cmd)
        Entrypoint   = $current.Config.Entrypoint
        WorkingDir   = $current.Config.WorkingDir
        User         = $current.Config.User
        Labels       = $labels
        ExposedPorts = $current.Config.ExposedPorts
        Healthcheck  = $current.Config.Healthcheck
        HostConfig   = @{
            PortBindings  = $current.HostConfig.PortBindings
            RestartPolicy = $current.HostConfig.RestartPolicy
            Mounts        = $mounts
            NetworkMode   = $current.HostConfig.NetworkMode
            Memory        = $current.HostConfig.Memory
            NanoCpus      = $current.HostConfig.NanoCpus
            SecurityOpt   = @($current.HostConfig.SecurityOpt)
            LogConfig     = $current.HostConfig.LogConfig
        }
    }
    $backupName = "$name-rollback-$((Get-Date).ToString('yyyyMMddHHmmss'))"
    $created = $null
    $renamed = $false
    try {
        Write-Host "Parando container anterior '$name'..."
        Invoke-PortainerApi -Method POST -Uri "$dockerBase/containers/$encodedName/stop?t=15" -ApiKey $ApiKey | Out-Null
        Invoke-PortainerApi -Method POST -Uri "$dockerBase/containers/$encodedName/rename?name=$([Uri]::EscapeDataString($backupName))" -ApiKey $ApiKey | Out-Null
        $renamed = $true
        $created = Invoke-PortainerApi -Method POST -Uri "$dockerBase/containers/create?name=$encodedName" -ApiKey $ApiKey -Body $payload
        Invoke-PortainerApi -Method POST -Uri "$dockerBase/containers/$($created.Id)/start" -ApiKey $ApiKey | Out-Null

        $deadline = (Get-Date).AddSeconds([int]$Settings.HealthCheck.TimeoutSeconds)
        do {
            Start-Sleep -Seconds 5
            $replacement = Invoke-PortainerApi -Method GET -Uri "$dockerBase/containers/$($created.Id)/json" -ApiKey $ApiKey
            $healthy = -not $replacement.State.Health -or $replacement.State.Health.Status -eq 'healthy'
        } while ((-not $replacement.State.Running -or -not $healthy) -and (Get-Date) -lt $deadline)
        if (-not $replacement.State.Running -or -not $healthy) {
            throw "Novo container nao ficou saudavel (estado=$($replacement.State.Status), health=$($replacement.State.Health.Status))."
        }
        Invoke-WebRequest -Method Delete -Uri "$dockerBase/containers/$([Uri]::EscapeDataString($backupName))?force=true&v=false" -Headers @{ 'X-API-Key' = $ApiKey } -UseBasicParsing -TimeoutSec 30 | Out-Null
        Write-Host "Container '$name' substituido por $Image"
    } catch {
        $failure = $_.Exception.Message
        if ($created) {
            try { Invoke-WebRequest -Method Delete -Uri "$dockerBase/containers/$encodedName?force=true&v=false" -Headers @{ 'X-API-Key' = $ApiKey } -UseBasicParsing -TimeoutSec 30 | Out-Null } catch {}
        }
        if ($renamed) {
            try {
                $backup = [Uri]::EscapeDataString($backupName)
                Invoke-PortainerApi -Method POST -Uri "$dockerBase/containers/$backup/rename?name=$encodedName" -ApiKey $ApiKey | Out-Null
                Invoke-PortainerApi -Method POST -Uri "$dockerBase/containers/$encodedName/start" -ApiKey $ApiKey | Out-Null
                Write-Host 'Rollback do container anterior concluido.'
            } catch {
                Write-Warning "Falha critica no rollback: $($_.Exception.Message)"
            }
        }
        throw $failure
    }
}

function Confirm-DeployedContainer {
    param(
        [hashtable]$Settings,
        [string]$ApiKey,
        [string]$ExpectedImage
    )
    $portainer = $Settings.Portainer
    $base = ([string]$portainer.Url).TrimEnd('/')
    $containerName = [Uri]::EscapeDataString([string]$portainer.ContainerName)
    $dockerBase = "$base/api/endpoints/$($portainer.EndpointId)/docker"
    $container = Invoke-PortainerApi -Method GET -Uri "$dockerBase/containers/$containerName/json" -ApiKey $ApiKey

    if (-not $container.State.Running) {
        throw "Container '$($portainer.ContainerName)' não está em execução."
    }
    if ($container.State.Health -and $container.State.Health.Status -ne 'healthy') {
        throw "Container '$($portainer.ContainerName)' com health '$($container.State.Health.Status)'."
    }
    if ([string]$container.Config.Image -ne $ExpectedImage) {
        throw "Imagem ativa '$($container.Config.Image)' difere da esperada '$ExpectedImage'."
    }

    $requiredMount = [string]$portainer.RequiredReadOnlyMount
    if ($requiredMount) {
        $mount = $container.Mounts | Where-Object { $_.Destination -eq $requiredMount } | Select-Object -First 1
        if (-not $mount) {
            throw "Mount obrigatório '$requiredMount' não encontrado no container."
        }
        if ($mount.RW) {
            throw "Mount '$requiredMount' está gravável; esperado somente leitura."
        }
        Write-Host "Mount confirmado: $($mount.Source):$requiredMount (somente leitura)"
    }

    $logsUri = "$dockerBase/containers/$containerName/logs?stdout=true&stderr=true&tail=100&timestamps=true"
    $logs = Invoke-WebRequest -Method Get -Uri $logsUri -Headers @{ 'X-API-Key' = $ApiKey } -UseBasicParsing -TimeoutSec 30
    if ($logs.StatusCode -ne 200) {
        throw "Não foi possível consultar os logs do container '$($portainer.ContainerName)'."
    }
    Write-Host "Container, imagem e logs confirmados: $($portainer.ContainerName)"
}

$configPath = (Resolve-Path -LiteralPath $Config).Path
$settings = Import-PowerShellDataFile -LiteralPath $configPath
$configDirectory = Split-Path -Parent $configPath
$repositoryPath = [IO.Path]::GetFullPath((Join-Path $configDirectory $settings.RepositoryPath))
$composePath = [IO.Path]::GetFullPath((Join-Path $repositoryPath $settings.ComposePath))
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
        if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel inspecionar os arquivos preparados.' }
        $blocked = @($staged | Where-Object {
            $_ -notmatch '(?i)(^|/)\.env\.example$' -and
            $_ -match '(?i)(^|/)(\.env($|\.)|.*\.(pfx|p12|pem|key|xlsx|xlsm|csv|sqlite|sqlite3|db)$|uploads?(/|$)|backups?(/|$)|reports?(/|$)|relatorios?(/|$)|credentials?(/|$))'
        })
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
        if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
            throw "Compose versionado não encontrado: $composePath"
        }
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

        $image = ([string]$portainer.ImageTemplate).Replace('{commit}', $commit)
        $deployed = $false
        if ($PSCmdlet.ShouldProcess($portainer.ContainerName, "publicar $image")) {
            Invoke-PortainerImagePull -Settings $settings -ApiKey $apiKey -Image $image
            Update-PortainerContainer -Settings $settings -ApiKey $apiKey -Image $image
            $deployed = $true
        }
        if ($deployed) {
            Wait-HealthCheck -Uri $settings.HealthCheck.Url -TimeoutSeconds ([int]$settings.HealthCheck.TimeoutSeconds)
            Confirm-DeployedContainer -Settings $settings -ApiKey $apiKey -ExpectedImage $image
        }
    }

    Write-Host "Concluido no commit $commit"
} finally {
    Pop-Location
}
