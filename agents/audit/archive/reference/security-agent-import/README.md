# Import de referência: Security Agent

Esta árvore contém somente o núcleo read-only selecionado de `security.zip`.
Ela é uma referência para a implementação oficial do agente Audit e **não é um
comando de produção**.

Foram importados:

- coletor original;
- consultas SQL de observação;
- política declarativa;
- documentação e dependências declaradas.

Foram deliberadamente excluídos:

- `demo-accounts.local.json` e qualquer configuração local de conta;
- scripts de provisionamento ou exercício controlado;
- simulador, caches Python, `.DS_Store` e metadados `__MACOSX`;
- relatórios, snapshots e demais saídas geradas.

O coletor desta referência ainda usa credenciais por variáveis de ambiente e
não deve ser executado. A implementação oficial deverá usar somente o perfil de
conexão aprovado por referência, manter TLS obrigatório, aplicar allowlist SQL,
restringir conclusões funcionais ao schema `sakila` e sanitizar erros antes de
persistir.

Veja `../../../docs/security-agent-import-assessment.md`.
