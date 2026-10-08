# Solicitação para o projeto de equalização — e-mail único entre ativos

Implemente no fluxo **4 — Analisar Email/SharePoint x RH** uma validação obrigatória de unicidade de
e-mail antes da publicação no SQLite corporativo.

## Regra funcional

Depois de montar a prévia final e aplicar todos os vínculos preservados, referência homologada e
Lorac, normalize cada e-mail com `trim` e minúsculas. Desconsidere valores vazios. Se o mesmo e-mail
válido estiver associado a mais de um colaborador com `ativo_rh = 1`, marque todas as linhas do grupo
como conflito e bloqueie a publicação.

A interface deve mostrar, para cada conflito, pelo menos: e-mail, nome, empresa/filial, matrícula,
origem do e-mail e método de vínculo. A mensagem deve explicar que identidade técnica única não
garante e-mail único e orientar a correção na fonte RH, referência homologada ou Lorac.

## Regras de publicação que devem permanecer documentadas

- O XLSX mais recente é a fonte dos colaboradores atuais, mas a publicação não limpa fisicamente o
  banco.
- A publicação faz UPSERT pela identidade técnica existente.
- Registros ausentes na nova carga são marcados como inativos e permanecem no histórico.
- Identidades técnicas repetidas no XLSX continuam bloqueando a publicação.
- Colaborador ativo sem e-mail permanece publicado com assinatura bloqueada.
- E-mails repetidos envolvendo apenas registros inativos não bloqueiam; qualquer grupo com dois ou
  mais ativos bloqueia.
- Backup, transação, auditoria do responsável e `PRAGMA integrity_check` continuam obrigatórios.

## Critérios de aceite

1. Dois ativos com o mesmo e-mail normalizado não podem ser publicados.
2. A comparação deve considerar diferenças de maiúsculas e espaços como o mesmo e-mail.
3. A prévia deve identificar todas as pessoas e a origem de cada vínculo conflitante.
4. Corrigido o conflito, a publicação deve prosseguir normalmente.
5. Um ativo e um inativo com o mesmo e-mail não devem criar duplicidade ativa.
6. Linhas sem e-mail não devem ser agrupadas como duplicadas.
7. O teste deve cobrir e-mail vindo de vínculo preservado, referência e Lorac.
8. A API/readiness e a visão `rh_assinaturas_colaboradores` não devem expor dois ativos aptos com o
   mesmo e-mail.

Atualize o README e `docs/BASE_CORPORATIVA_RH_EMAIL_ASSINATURAS.md` do projeto de equalização com
essas regras e inclua testes automatizados para todos os critérios acima.

