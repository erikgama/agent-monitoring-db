# Política: nenhuma ação automática

- Alertas são somente informação de entrada para avaliação futura do DBA.
- Recebimento, validação ou severidade crítica não autorizam alteração de banco,
  parâmetros, usuários, privilégios, replicação, queries ou infraestrutura.
- Nenhuma query corretiva deve ser gerada ou executada automaticamente.
- Qualquer capacidade futura de recomendação ou ação deverá ser projetada
  explicitamente, validada, auditável e submetida às regras de autorização do
  DBA e do projeto.
- Payload inválido deve ser rejeitado e registrado pelo componente futuro de
  integração; nunca deve ser corrigido por inferência silenciosa.
