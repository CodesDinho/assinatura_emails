# Geração de assinatura de e-mail Dinho

Aplicação Flask para consultar colaboradores, gerar a assinatura corporativa e enviá-la por e-mail. A fonte oficial é a publicação homologada de RH no SQLite corporativo compartilhado. A aplicação abre essa base somente para leitura e não faz fallback silencioso para a planilha legada.

## Execução local

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m flask --app wsgi run --host 0.0.0.0 --port 5000
```

A página pública fica em `http://localhost:5000`. A área interna fica em `http://localhost:5000/admin/login`.

## Container

O container roda como usuário sem privilégios e não inclui `.env`, banco ou planilhas na imagem. O
GitHub Actions testa o projeto e publica imagens imutáveis no GHCR. O Portainer do servidor
`10.1.1.153` executa a stack na porta `8505`, usando os volumes externos
`assinatura-emails-data` (cargas pendentes e rollback legado) e `assinatura-emails-instance` (logs e arquivos temporários).

A stack monta `/home/administrator/Desktop/codes/ti_dinho_slack/shared_data` em `/shared:ro` e lê `/shared/slack_apps.db`. O serviço só fica saudável quando existem `rh_importacoes`, a view `rh_assinaturas_colaboradores` e uma publicação `PUBLICADO`. Verifique em `http://10.1.1.153:8505/health`.

O upload administrativo apenas valida e guarda o arquivo em `/app/data/pending_imports`; ele não grava no SQLite nem altera vínculos publicados. Para efetivar uma carga, solicite à TI a etapa 4 — Analisar Email/SharePoint x RH — no projeto de equalização e a publicação da prévia homologada. A variável `EMPLOYEE_WORKBOOK_PATH` existe somente para rollback controlado e não participa do fluxo normal.

Correções feitas em **Editar** na área administrativa são gravadas em `/app/instance/employee_overrides.db` e aplicadas sobre a publicação somente para este sistema. O banco corporativo permanece intacto; a origem também deve ser corrigida pelo RH para que futuras publicações tragam o dado correto.

Uma nova publicação não apaga o histórico do SQLite: atualiza ou inclui os colaboradores da carga e
marca como inativos os ausentes. Identidades técnicas repetidas devem impedir a publicação. Além
disso, um mesmo e-mail válido não pode pertencer a dois colaboradores ativos; o projeto de
assinaturas bloqueia consultas nessa condição como defesa adicional. Consulte
[as regras da publicação de RH](docs/REGRAS_PUBLICACAO_RH.md).

Para produção com HTTPS, configure `SESSION_COOKIE_SECURE=true`. Não versione `.env`, arquivos Excel nem PowerPoint.

## Testes

```powershell
python -m pytest -q
```

## Commit e deploy automatizados

O gatilho reutilizável executa testes, commit, push, aguarda a imagem do GitHub Actions,
atualiza a stack no Portainer e valida a saúde da aplicação:

```powershell
.\scripts\commit-deploy.ps1 -Message "Descreva a alteração"
```

Consulte [docs/CATALOGO_COMMIT_DEPLOY.md](docs/CATALOGO_COMMIT_DEPLOY.md) para configuração,
uso em outros projetos, segurança e recuperação.

## Estrutura

- `app/`: rotas, templates e serviços;
- `assets/`: fonte e elementos gráficos da assinatura;
- `config/users.json`: usuários internos com senhas em hash PBKDF2;
- `data/`: cargas pendentes e artefatos de rollback, ignorados pelo Git;
- `instance/`: logs de solicitações e arquivos temporários, ignorados pelo Git;
- `tests/`: testes automatizados.
