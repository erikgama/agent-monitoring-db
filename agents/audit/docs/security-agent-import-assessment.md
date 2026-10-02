# Avaliação inicial do `security.zip`

- Data: 2026-09-16
- Origem: artefato fornecido pelo DBA em `security.zip`
- Estado: núcleo read-only importado como referência não executável
- Escopo funcional obrigatório: `sakila`

## O que foi aproveitado como referência

- separação entre consultas, política, coleta e renderização;
- geração pareada de `latest.json`, `summary.json` e `latest.html`;
- gravação por arquivo temporário seguida de substituição;
- paginação de `audit_log_read()` na mesma sessão;
- pseudonimização de identidade, origem e SQL;
- política declarativa em modo `detect_only`;
- distinção entre domínio indisponível, evidência truncada e condição saudável.

## Compatibilidade confirmada com a base atual

Uma verificação read-only ao vivo em 2026-09-16 confirmou:

- MySQL Enterprise Cloud `26.7.0-cloud`;
- plugin Enterprise Audit `audit_log` ativo;
- formato JSON e estratégia assíncrona;
- filtro único `sakila_security_monitoring`, JSON válido e atribuído como
  padrão;
- filtro menciona `sakila`, conexões, desconexões, DML, `GRANT`, `REVOKE` e
  `TRUNCATE`, sem `abort` e sem referência a outro schema funcional;
- leitura de eventos disponível, sem erro, pela interface `audit_log_read()`.

Evidência: `../evidence/2026-09-16/audit-live-validation-2026-09-16T143317Z.json`.

Além do Enterprise Audit, o pacote de referência consulta:

- `performance_schema` para conexões, TLS e variáveis globais;
- `information_schema` para plugin, privilégios e capacidade das tabelas Audit;
- `mysql.user`, `mysql.global_grants` e `mysql.role_edges` para inventário
  global de contas, privilégios e roles;
- política JSON determinística para controles de TLS, contas e privilégios;
- renderização local de JSON, resumo JSON e HTML.

O preflight importado confirma apenas a existência das tabelas de filtro. A
implementação oficial manterá a validação mais forte já existente no Audit,
que confirma nome, atribuição e propriedades seguras do filtro sem persistir o
JSON integral.

## O que não pode entrar diretamente em produção

- senha e identidade de conexão recebidas por variáveis de ambiente;
- scripts que criam contas ou geram DDL/DML para demonstração;
- arquivo local de contas da demo;
- resultados, caches e metadados do macOS incluídos no ZIP;
- eventos DDL e erros globais sem comprovação de vínculo com `sakila`;
- mensagem bruta do conector MySQL persistida em erros de domínio;
- exigência de `AUDIT_ADMIN` tratada como regra de saúde sem separar capacidade
  mínima de leitura de privilégio administrativo;
- consultas fora de uma allowlist oficial do agente.

## Fundação da implementação oficial

1. O material importado permanece em `reference/` e nunca é executado.
2. Consultas oficiais continuarão sob controle do agente e serão verificadas
   como somente leitura antes de qualquer conexão.
3. O Audit observará a mesma instância MySQL HeatWave e o mesmo schema `sakila`
   usados pelo Health Check. A conexão reutilizará exclusivamente o perfil já
   aprovado por referência; o agente não receberá nem persistirá senha.
4. TLS será obrigatório e erros serão reduzidos a código seguro, sem host,
   conta ou mensagem bruta.
5. Eventos funcionais só poderão entrar em achados quando forem atribuíveis ao
   schema `sakila`; sinais globais serão identificados como estado da instância.
6. JSON e HTML da mesma coleta compartilharão `audit_id` e horário de coleta.
7. Nenhuma política poderá executar SQL, modificar filtro Audit ou agir no
   banco.

## Limites desta etapa

Nenhum arquivo do ZIP foi executado e nenhuma conexão MySQL foi aberta. Não
houve criação de conta, alteração de filtro, DDL, DML, simulação de ataque ou
leitura de credencial. O próximo incremento deve transformar apenas as partes
aprovadas em módulos oficiais testáveis, sem promover o coletor importado por
cópia direta.
