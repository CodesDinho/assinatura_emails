# Geração de assinatura de e-mail Dinho

Aplicação Flask para consultar colaboradores, gerar a assinatura corporativa e enviá-la por e-mail. A planilha Excel oficial é a única fonte dos colaboradores; a área administrativa lê e grava diretamente nela.

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
`assinatura-emails-data` (planilha e backups) e `assinatura-emails-instance` (logs e arquivos temporários).

A planilha oficial deve ser carregada no volume de dados como
`colaboradores_ativos_2409.xlsx`. Toda alteração gera antes um backup em `/app/data/backups` e substitui a planilha atomicamente. Verifique a saúde em `http://10.1.1.153:8505/health`.

Para produção com HTTPS, configure `SESSION_COOKIE_SECURE=true`. Não versione `.env`, arquivos Excel nem PowerPoint.

## Testes

```powershell
python -m pytest -q
```

## Estrutura

- `app/`: rotas, templates e serviços;
- `assets/`: fonte e elementos gráficos da assinatura;
- `config/users.json`: usuários internos com senhas em hash PBKDF2;
- `data/`: planilha operacional local, ignorada pelo Git;
- `instance/`: logs de solicitações e arquivos temporários, ignorados pelo Git;
- `tests/`: testes automatizados.
