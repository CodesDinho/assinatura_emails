# Integração com a base RH central

O projeto de assinaturas não mantém uma cópia própria da base de colaboradores. Em produção, ele monta `/shared` somente para leitura e consulta diretamente o SQLite publicado pelo projeto de equalização.

## Contrato operacional

- Fonte: `SHARED_SQLITE_PATH`, normalmente `/shared/slack_apps.db`.
- Publicação válida: último registro de `rh_importacoes` com status `PUBLICADO`.
- Consulta: view `rh_assinaturas_colaboradores`.
- Liberação da assinatura: colaborador ativo, e-mail válido e sem duplicidade entre ativos.
- Falha segura: sem publicação válida, a consulta pública e o healthcheck ficam bloqueados.
- Escrita: este projeto nunca altera tabelas `rh_*` e monta `/shared` como `:ro`.

## Atualização dos colaboradores

Não existe carga local neste projeto. Inclusões, alterações e publicações são realizadas exclusivamente no projeto de equalização. O projeto de assinaturas consome diretamente a view corporativa publicada.

O painel administrativo exibe a última publicação reconhecida e os totais de colaboradores ativos, aptos para assinatura e sem e-mail válido. Não existe sincronização manual entre os dois projetos.
