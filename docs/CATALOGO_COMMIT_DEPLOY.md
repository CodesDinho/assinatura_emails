# Gatilho de commit e deploy via Git + Portainer

## Finalidade

Automatiza o fluxo operacional de aplicações mantidas em Git e publicadas pelo Portainer:

1. executa os testes configurados;
2. prepara e cria o commit;
3. envia a branch ao repositório remoto;
4. aguarda o workflow do GitHub Actions concluir;
5. atualiza a imagem configurada na stack do Portainer;
6. publica o `compose.yaml` versionado, solicita `pull` da imagem e recria somente os serviços necessários;
7. confirma o endpoint de saúde, a imagem ativa, o estado do container, mounts obrigatórios e acesso aos logs.

O processo não depende do Codex. A chave do Portainer e tokens nunca devem ser salvos no arquivo de configuração versionado.

## Uso neste projeto

Na raiz do repositório, execute:

```powershell
.\scripts\commit-deploy.ps1 -Message "Descreva a alteração"
```

Opções úteis:

```powershell
# Simula operações mutáveis do Portainer; testes e comandos Git ainda são executados.
.\scripts\commit-deploy.ps1 -Message "Teste do deploy" -WhatIf

# Publica novamente o commit atual, sem criar outro commit.
.\scripts\commit-deploy.ps1 -Message "Redeploy" -SkipCommit

# Faz somente teste, commit e push.
.\scripts\commit-deploy.ps1 -Message "Atualiza documentação" -SkipDeploy
```

O arquivo `deploy.config.psd1` contém o endereço do Portainer, endpoint, stack, branch, workflow, padrão da imagem e health check deste serviço. A chave é lida de `PORTAINER_API_KEY` no ambiente ou do `.env` local ignorado pelo Git.

## Adoção em outro projeto

Copie os arquivos abaixo para o novo repositório:

- `scripts/commit-deploy.ps1`;
- `deploy.config.psd1`.

Depois ajuste em `deploy.config.psd1`:

| Campo | Origem da informação |
|---|---|
| `RepositoryPath` | raiz do Git, relativa ao arquivo de configuração |
| `Remote` / `Branch` | `git remote -v` e branch de produção |
| `ComposePath` | arquivo Compose versionado que será enviado ao Portainer |
| `TestCommands` | comandos de teste obrigatórios do projeto |
| `GitHubActions.Repository` | organização e repositório no GitHub |
| `GitHubActions.Workflow` | nome ou arquivo do workflow que publica a imagem |
| `Portainer.Url` | URL interna do Portainer |
| `Portainer.EndpointId` | número exibido na URL `#!/<endpoint>/...` |
| `Portainer.StackId` | ID da stack, obtido no Portainer/API |
| `Portainer.RegistryId` | ID do registro privado cadastrado no Portainer |
| `Portainer.PullTimeoutSeconds` | prazo máximo para baixar a imagem explicitamente |
| `Portainer.UpdateTimeoutSeconds` | prazo máximo para atualizar a stack |
| `ImageEnvironmentVariable` | variável da stack que contém a imagem imutável |
| `ImageTemplate` | imagem com `{commit}` no lugar do SHA |
| `ContainerName` | nome do container validado após o deploy |
| `RequiredReadOnlyMount` | destino que deve existir como mount somente leitura |
| `HealthCheck.Url` | endpoint HTTP que retorna sucesso após o deploy |

Configure a credencial apenas na máquina de execução:

```powershell
$env:PORTAINER_API_KEY = 'ptr_...'
```

Para repositório privado, também configure um token capaz de consultar Actions:

```powershell
$env:GITHUB_TOKEN = 'github_pat_...'
```

## Pré-requisitos da stack

A stack deve usar uma variável para a imagem, por exemplo:

```yaml
services:
  app:
    image: ${APP_IMAGE}
```

No Portainer, cadastre `APP_IMAGE` e configure no gatilho:

```powershell
ImageEnvironmentVariable = 'APP_IMAGE'
ImageTemplate = 'ghcr.io/empresa/projeto:sha-{commit}'
```

O pipeline deve publicar exatamente esse padrão de tag. Tags imutáveis por SHA permitem auditar e reverter cada versão.

## Segurança e comportamento em falhas

- `.env`, certificados e chaves conhecidos são bloqueados se entrarem no stage.
- Arquivos ignorados pelo Git, como a planilha operacional deste projeto, não entram no commit.
- Falha em testes, push, pipeline, Portainer ou health check interrompe o processo com código diferente de zero.
- A imagem é baixada explicitamente com a credencial do registro antes da atualização; a chamada da stack possui timeout e não aguarda indefinidamente.
- A stack mantém suas variáveis existentes; somente a variável da imagem é alterada.
- Volumes persistentes não são removidos pelo deploy.
- Para reverter, execute novamente com o commit desejado ajustando temporariamente `ImageTemplate`, ou restaure a imagem anterior diretamente na stack.

## Dados persistentes e base corporativa

O SQLite corporativo não faz parte da imagem nem do Git. Ele é montado do host em `/shared:ro` e deve ser publicado pelo projeto de equalização. Antes do deploy, confirme uma publicação `PUBLICADO` em `rh_importacoes` e a view `rh_assinaturas_colaboradores`.

Uploads feitos nesta aplicação ficam em `/app/data/pending_imports` e não alteram a base publicada. Após um upload, a TI deve executar a etapa 4 — Analisar Email/SharePoint x RH — no projeto de equalização, homologar a prévia e publicá-la. A planilha legada permanece apenas para rollback controlado.

Após atualizar a stack, valide o endpoint `/health`, o estado do container, o mount `/shared:ro` e os logs. Um `503` com aviso de equalização ou indisponibilidade deve interromper a entrega; não habilite fallback automático para XLSX.
