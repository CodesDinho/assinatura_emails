@{
    # Caminho relativo a este arquivo. Em outros projetos, normalmente permanece '.'.
    RepositoryPath = '.'
    Remote = 'origin'
    Branch = 'main'

    TestCommands = @(
        'python -m pytest -q'
    )

    GitHubActions = @{
        Enabled = $true
        Repository = 'CodesDinho/assinatura_emails'
        Workflow = 'build-image.yml'
        TimeoutSeconds = 900
        # Opcional para repositórios privados ou limite de API: variável de ambiente com token GitHub.
        TokenEnvironmentVariable = 'GITHUB_TOKEN'
    }

    Portainer = @{
        Url = 'http://10.1.1.153:9090'
        EndpointId = 3
        StackId = 11
        ApiKeyEnvironmentVariable = 'PORTAINER_API_KEY'
        # Alternativa local conveniente; este arquivo já é ignorado pelo Git.
        ApiKeyDotEnvPath = '.env'
        ImageEnvironmentVariable = 'ASSINATURA_EMAILS_IMAGE'
        ImageTemplate = 'ghcr.io/codesdinho/assinatura-emails:sha-{commit}'
    }

    HealthCheck = @{
        Url = 'http://10.1.1.153:8505/health'
        TimeoutSeconds = 180
    }
}
