# Playbook de investigação Audit

O Audit responde somente ao DBA e opera em modo somente leitura.

## Perguntas suportadas

- Houve conexões falhas ou incomuns na janela?
- Quem escreveu em tabelas do schema `sakila`?
- Houve DDL, `GRANT`, `REVOKE` ou mudança administrativa?
- Qual identidade mascarada, origem mascarada, horário, sessão mascarada e
  fingerprint SQL estão associados ao evento?

## Pré-validação obrigatória

1. Confirmar versão e edição do MySQL ao vivo.
2. Confirmar plugin/componente ativo e se Audit está habilitado.
3. Confirmar formato, destino, estratégia e políticas sem alterá-los.
4. Confirmar a existência e atribuição de `sakila_security_monitoring`.
5. Verificar que a definição menciona `sakila` e não contém `abort`.
6. Testar apenas a capacidade de leitura limitada existente.

Use uma coleta oficial nova para essa captura:

```sh
uv run mysql-audit-security collect
```

O coletor não lê o conteúdo do perfil local; apenas fornece sua referência ao
cliente MySQL e grava o resultado mascarado em `audit_security/results/`.

## Regras para eventos

- Limitar janela e quantidade de eventos.
- Não criar eventos artificiais com login inválido, DDL, DML ou privilégios.
- Mascarar usuário, host, IP, sessão e SQL antes de gravar.
- Preservar timestamp, classe, ação, status, schema/tabela quando disponíveis e
  um fingerprint irreversível do SQL.
- Declarar a cobertura real do filtro e a ausência de eventos observados
  separadamente.

## Documentação oficial

- Leitura de Audit Log: https://dev.mysql.com/doc/refman/8.4/en/audit-log-file-reading.html
- Referência do plugin Audit: https://dev.mysql.com/doc/refman/8.0/en/audit-log-reference.html
- Enterprise Audit 26.7: https://dev.mysql.com/doc/refman/26.7/en/audit-log-component.html
