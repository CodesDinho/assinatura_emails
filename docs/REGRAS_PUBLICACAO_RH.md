# Regras da publicação de RH consumida pelas assinaturas

## O que uma nova publicação representa

A planilha mais recente de colaboradores ativos é a fonte operacional da publicação. Isso não
significa apagar fisicamente o banco e recriá-lo do zero. O projeto de equalização atualiza ou inclui
as identidades encontradas e marca como inativos os registros que não aparecem na carga mais recente,
preservando histórico e rastreabilidade.

O sistema de assinaturas consome somente colaboradores ativos pela visão
`rh_assinaturas_colaboradores`.

## Identidade do colaborador

A identidade técnica é formada por empresa/filial, matrícula e um discriminador irreversível baseado
em CPF ou, quando necessário, no nome normalizado. Duas linhas com a mesma identidade técnica devem
bloquear a publicação até que o XLSX seja corrigido.

Identidade única não é a mesma coisa que e-mail único: duas pessoas tecnicamente distintas ainda
podem receber o mesmo e-mail por erro de vínculo.

## Regra obrigatória de unicidade de e-mail

Um e-mail corporativo válido não pode estar associado a mais de um colaborador ativo. A prévia deve:

1. normalizar o e-mail (`trim` e minúsculas);
2. ignorar valores vazios ao verificar duplicidade;
3. listar todas as pessoas envolvidas em cada conflito;
4. bloquear a confirmação/publicação enquanto existir qualquer conflito;
5. orientar a correção na fonte do RH ou no vínculo Lorac antes de publicar.

Como defesa adicional, o sistema de assinaturas também bloqueia consulta e envio quando encontra o
mesmo e-mail em mais de um colaborador ativo.

## Edições e exclusões locais

As ações **Editar** e **Excluir** da área administrativa não alteram o SQLite corporativo, que é
montado como somente leitura. Elas criam correções persistentes em
`/app/instance/employee_overrides.db`, aplicadas somente neste sistema.

- **Editar** substitui os campos exibidos pelo sistema de assinaturas.
- **Excluir** oculta o colaborador neste sistema, mas não apaga o registro corporativo.
- A correção definitiva deve ser feita pelo RH e entrar em uma nova publicação homologada.

## Critérios mínimos antes da publicação

- XLSX de origem identificado e hash calculado;
- nenhuma identidade técnica duplicada;
- nenhum e-mail válido duplicado entre colaboradores ativos;
- linhas sem e-mail mantidas com status de assinatura bloqueada;
- ausentes da carga anterior marcados como inativos, sem exclusão física;
- prévia revisada e confirmação explícita do responsável;
- backup criado antes da transação e `PRAGMA integrity_check` válido após o commit.

