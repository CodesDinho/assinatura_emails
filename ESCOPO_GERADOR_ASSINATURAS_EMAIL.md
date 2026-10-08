# Escopo — Gerador e envio de assinaturas corporativas por e-mail

## 1. Objetivo

Criar uma aplicação web interna para que cada colaborador informe seu e-mail corporativo, confira os
dados oficiais de nome e cargo e solicite o envio de sua assinatura pronta para o próprio e-mail.

O colaborador não poderá editar nome, cargo, e-mail, identidade visual, posição, fonte, cores ou
qualquer outro elemento da assinatura. Correções devem ser feitas exclusivamente na base administrada
pelo RH/TI.

Slack está fora do escopo, pois nem todos os colaboradores possuem acesso.

## 2. Perfis

### Colaborador

- informa o próprio e-mail corporativo;
- visualiza nome e cargo encontrados na base oficial;
- confirma que os dados estão corretos;
- solicita a assinatura;
- recebe a imagem pronta exclusivamente no e-mail informado e cadastrado;
- não altera nenhum dado pela aplicação.

### RH/TI administrador

- envia uma lista XLSX/CSV como carga pendente para o fluxo de equalização;
- consulta registros válidos, inválidos e duplicados antes de confirmar a importação;
- aplica correções operacionais locais sem alterar a base corporativa somente leitura;
- solicita ao RH a correção definitiva na fonte e uma nova publicação homologada;
- consulta histórico de solicitações e falhas de envio;
- não visualiza nem armazena senha de e-mail do colaborador.

## 3. Fonte de dados

Planilha fornecida pela Cassiane/RH, inicialmente com:

| Campo | Obrigatório | Regra |
|---|---:|---|
| Nome completo | Sim | Preservar acentos; remover espaços duplicados |
| Cargo | Sim | Texto oficial definido pelo RH |
| E-mail corporativo | Sim | Identificador único, convertido para minúsculas |
| Ativo | Recomendado | `SIM` ou `NÃO`; ausente significa ativo somente na primeira versão |
| Telefone | Opcional | Usado apenas se a identidade visual exigir valor individual |

Rejeitar a publicação definitiva quando houver identidade técnica repetida, e-mail válido associado a
mais de um colaborador ativo, e-mail inválido ou linha sem nome ou cargo. Exibir uma prévia dos
problemas antes de gravar. Uma nova publicação atualiza/inclui os presentes e marca os ausentes como
inativos; ela não apaga fisicamente o histórico do banco.

## 4. Fluxo do colaborador

1. Acessar a URL interna fixa.
2. Informar o e-mail corporativo.
3. O backend normaliza o e-mail e procura correspondência exata em registro ativo.
4. Se encontrado, mostrar nome e cargo somente para conferência.
5. O colaborador confirma em **Enviar minha assinatura**.
6. O backend gera a imagem a partir do modelo oficial.
7. A imagem é enviada apenas ao e-mail usado na consulta; não deve existir campo de destinatário.
8. Mostrar uma resposta genérica de sucesso, sem link público permanente para a imagem.
9. Registrar data/hora, e-mail normalizado, resultado do envio e identificador da solicitação, sem
   registrar conteúdo secreto.

O e-mail não encontrado deve produzir mensagem genérica, evitando informar detalhes excessivos da
base. Como a tela confirma nome e cargo, a aplicação deve ficar restrita à rede interna, possuir limite
de tentativas e registrar consultas abusivas.

## 5. Geração da assinatura

- usar Python e Pillow para compor a imagem no servidor;
- manter o arquivo matriz em `assets/assinatura_padrao.jpg` ou preferencialmente PNG;
- utilizar uma matriz limpa, sem nome, cargo e e-mail de outro colaborador;
- guardar as fontes licenciadas em `assets/fonts/` quando a licença permitir;
- definir coordenadas, tamanhos, cores e espaçamentos em configuração versionada;
- reduzir dinamicamente a fonte de nomes/cargos longos dentro de limites aprovados;
- nunca cortar texto sem indicar erro nos testes;
- preservar resolução e qualidade suficientes para assinatura de e-mail;
- gerar nome de arquivo seguro, por exemplo `assinatura_nome_sobrenome.png`;
- excluir arquivos temporários após envio, mesmo quando ocorrer falha;
- não gravar todas as assinaturas permanentemente se elas puderem ser regeneradas.

O arquivo atual de referência está em:

`C:\Users\roberson.souza\Downloads\assinatura_padrao.jpg`

Ele contém dados do Roberson e deve ser tratado como referência visual. Antes da implementação final,
obter com o Marketing/RH uma matriz limpa e, se possível, o arquivo original em alta resolução e as
fontes oficiais.

## 6. Envio de e-mail

- SMTP ou serviço corporativo já aprovado pela empresa;
- configurações exclusivamente por variáveis de ambiente/secrets;
- remetente corporativo identificado;
- assunto sugerido: `Sua assinatura de e-mail — Dinho Distribuidora`;
- anexar a imagem pronta e incluir instrução curta de instalação;
- enviar somente para o e-mail consultado e cadastrado;
- timeout e tentativas limitadas;
- não registrar credenciais nem conteúdo completo da mensagem em logs;
- diferenciar falha de geração, configuração e SMTP para suporte técnico.

## 7. Administração e autenticação

A área de colaborador pode operar sem senha por enviar o resultado somente ao endereço cadastrado,
desde que esteja disponível apenas na rede interna e protegida por rate limit.

A área administrativa deve exigir autenticação. Preferências:

1. Microsoft Entra ID/conta corporativa, se disponível;
2. autenticação administrativa já padronizada pela TI;
3. credencial local protegida por hash apenas como solução temporária.

Nunca colocar senha administrativa no HTML ou JavaScript do navegador.

## 8. Segurança obrigatória

- HTTPS quando publicado por domínio/reverse proxy;
- proteção CSRF nos formulários administrativos;
- rate limit por IP e e-mail nas solicitações;
- mensagem genérica para e-mails ausentes/inativos;
- comparação normalizada e exata do e-mail;
- nenhuma possibilidade de escolher outro destinatário;
- validação de tipo, tamanho e colunas do arquivo importado;
- prevenção de path traversal no nome dos arquivos;
- cabeçalhos seguros e cookies `HttpOnly`, `Secure` e `SameSite` quando aplicável;
- logs sem senha, token, conteúdo da planilha ou imagem em base64;
- `.env`, planilha real, banco, imagens geradas e logs no `.gitignore`;
- fornecer `.env.example` e planilha de exemplo com dados fictícios;
- backup da base antes de cada importação confirmada;
- política de retenção para logs e backups.

## 9. Tecnologia sugerida

- Python 3.12;
- Flask ou FastAPI com templates HTML simples;
- Pillow para composição da imagem;
- `openpyxl`/`pandas` para XLSX e biblioteca padrão para CSV;
- SQLite para cadastro importado, auditoria e controle administrativo;
- biblioteca SMTP ou integração corporativa aprovada;
- `pytest` para testes;
- Docker e Compose desde o início;
- Git privado desde o primeiro commit.

Evitar Streamlit para esta solução, pois há fluxo administrativo, controle de sessão, importação,
rate limit e envio transacional de e-mail.

## 10. Estrutura mínima do projeto

```text
gerador_assinaturas/
├── app/
│   ├── routes/
│   ├── services/
│   │   ├── signature_generator.py
│   │   ├── employee_importer.py
│   │   └── email_sender.py
│   ├── templates/
│   └── static/
├── assets/
│   ├── assinatura_padrao.jpg
│   └── fonts/
├── instance/                 # ignorado pelo Git
├── tests/
├── .env.example
├── .gitignore
├── Dockerfile
├── compose.yml
├── README.md
├── AGENTS.md
└── requirements.txt ou pyproject.toml
```

## 11. Porta, publicação e catálogo

- antes de escolher a porta, consultar o painel de portas do catálogo;
- definir servidor de destino e reservar uma combinação `servidor:porta` ainda livre;
- cadastrar ID, finalidade, Docker, Git, servidor, URL, pasta e responsável em
  `SERVICOS E CODIGOS.xlsx`;
- não reutilizar porta ocupada nem publicar antes de o painel permanecer verde;
- preferir domínio interno por reverse proxy em vez de divulgar IP/porta ao usuário final;
- documentar healthcheck, logs, backup, atualização e rollback;
- depois da implantação, atualizar catálogo, auditoria de containers e histórico documental.

## 12. Testes mínimos

### Dados

- importação válida de XLSX e CSV;
- rejeição de e-mail duplicado ou inválido;
- rejeição de nome/cargo ausente;
- normalização de maiúsculas e espaços;
- colaborador inativo não recebe assinatura.

### Imagem

- nomes curtos, médios, longos e com acentos;
- cargos longos e caracteres especiais;
- texto dentro da área autorizada;
- imagem final com dimensões e formato esperados;
- arquivo temporário removido após sucesso e falha.

### Segurança e envio

- destinatário não pode ser alterado pelo navegador;
- e-mail inexistente retorna resposta genérica;
- rate limit bloqueia repetição abusiva;
- área administrativa exige autenticação;
- nenhum segredo aparece em logs;
- SMTP simulado nos testes, sem enviar mensagens reais;
- teste de integração controlado para endereço da TI antes da produção.

## 13. Critérios de aceite

- RH consegue importar a lista e corrigir erros antes de confirmar;
- colaborador encontra somente o cadastro associado ao e-mail informado;
- nome, cargo e e-mail não são editáveis;
- assinatura respeita o modelo aprovado inclusive com textos longos;
- envio ocorre exclusivamente para o endereço cadastrado;
- aplicação apresenta mensagens claras sem expor dados desnecessários;
- logs permitem diagnóstico sem guardar segredos;
- testes automatizados passam;
- container possui healthcheck e configuração externa;
- README contém desenvolvimento, implantação, operação, backup e rollback;
- Git, Docker, porta e catálogo estão atualizados.

## 14. Fora do escopo inicial

- integração com Slack;
- editor livre de assinatura;
- envio para e-mail diferente do cadastro;
- criação automática de contas de e-mail;
- instalação automática da assinatura no Outlook;
- múltiplos modelos escolhidos pelo colaborador;
- acesso público pela internet sem autenticação corporativa.

## 15. Prompt para iniciar em um novo VS Code

Copie o texto abaixo para a nova sessão:

> Crie uma nova aplicação seguindo integralmente o arquivo
> `ESCOPO_GERADOR_ASSINATURAS_EMAIL.md`. Antes de implementar, leia todos os `AGENTS.md` aplicáveis,
> confirme a pasta oficial, o repositório Git, o servidor e uma porta livre no catálogo. Use Python,
> aplicação web tradicional, Pillow, SQLite, Docker e testes. Não use Slack nem Streamlit. O usuário
> deve informar o e-mail corporativo, conferir nome e cargo vindos da base oficial e solicitar o envio
> da assinatura pronta exclusivamente para esse mesmo endereço, sem editar dados. Trate
> `C:\Users\roberson.souza\Downloads\assinatura_padrao.jpg` apenas como referência até existir uma
> matriz limpa. Não use credenciais reais durante o desenvolvimento e não faça deploy, commit ou push
> sem validar o escopo e a autorização correspondente. Ao final, atualize README, catálogo, porta,
> documentação operacional e histórico conforme as boas práticas centrais.
