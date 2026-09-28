# Geração de assinatura de e-mail Dinho

Aplicação Flask para consultar colaboradores, gerar a assinatura corporativa e enviá-la por e-mail. A área administrativa autenticada permite editar e cadastrar colaboradores, com persistência no SQLite e na planilha oficial.

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
`assinatura-emails-data` e `assinatura-emails-instance`.

A planilha oficial deve ser carregada no volume de dados como
`colaboradores_ativos_2409.xlsx`. Verifique a saúde em `http://10.1.1.153:8505/health`.

Para produção com HTTPS, configure `SESSION_COOKIE_SECURE=true`. Não versione `.env`, arquivos Excel, PowerPoint nem o banco SQLite.

## Testes

```powershell
python -m pytest -q
```

## Estrutura

- `app/`: rotas, templates e serviços;
- `assets/`: fonte e elementos gráficos da assinatura;
- `config/users.json`: usuários internos com senhas em hash PBKDF2;
- `data/`: planilha operacional local, ignorada pelo Git;
- `instance/`: banco SQLite local, ignorado pelo Git;
- `tests/`: testes automatizados.
