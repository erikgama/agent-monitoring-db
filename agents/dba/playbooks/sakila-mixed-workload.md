# Workload misto controlado do Sakila (legado)

> Para simular o fluxo de locacao realista solicitado, use
> [`sakila-realistic-workload.md`](sakila-realistic-workload.md) e o executor
> `../scripts/sakila_realistic_workload.py`. Este playbook descreve o executor
> anterior e permanece apenas para rastreabilidade.

## Objetivo

Simular por dez minutos um fluxo transacional com `SELECT`, `INSERT`, `UPDATE`
e `DELETE` no schema `sakila` do MySQL HeatWave gerenciado. Este e um teste de
carga controlada; nao e um stress test, pois duracao, concorrencia e quantidade
maxima de transacoes possuem limites explicitos.

## Artefato

- Executor: `../scripts/sakila_mixed_workload.py`
- Configuração central: `../../../config/agent-monitoring.toml`
- Login path: `workload_login_path` da configuração central
- Alvo configurado: `alvo definido no perfil central, schema `sakila``, TCP e TLS obrigatorios

O perfil e apenas referenciado. Seu conteudo nunca deve ser aberto, copiado ou
incluido em logs e relatorios.

## Fluxo por transacao

1. Executa `USE sakila`.
2. Seleciona e bloqueia um inventario disponivel.
3. Le dados de catalogo e historico do cliente.
4. Insere um aluguel e um pagamento sinteticos.
5. Le o fluxo criado.
6. Atualiza o retorno e o valor do pagamento sintetico.
7. Exclui primeiro o pagamento e depois o aluguel sintetico.
8. Executa `COMMIT`.

O script nunca atualiza nem exclui linhas preexistentes. Uma interrupcao antes
do `COMMIT` encerra a conexao e deixa o rollback a cargo do MySQL. O unico
efeito persistente esperado e o avanco dos `AUTO_INCREMENT` de `rental` e
`payment`.

## Protecoes

- Sem `--execute`, o comando e somente dry-run e nao conecta ao banco.
- A execucao exige tambem `--confirm-target sakila`.
- O preflight recusa schema/porta inesperados, instancia read-only, ambiente
  que nao se identifica como Cloud/HeatWave ou ausencia das tabelas exigidas.
- Concorrencia limitada a oito workers, duracao limitada a uma hora e limite
  absoluto de transacoes.
- Cada transacao tem timeout de 45 segundos e erros persistidos sao
  sanitizados.
- Nenhum DDL, GRANT, REVOKE ou mudanca de configuracao e executado.

## Validacao sem DML

```sh
python3 ../scripts/sakila_mixed_workload.py
```

## Execucao padrao de dez minutos

Requer autorizacao especifica separada para executar DML:

```sh
python3 ../scripts/sakila_mixed_workload.py \
  --execute \
  --confirm-target sakila \
  --duration-seconds 600 \
  --workers 8 \
  --think-time-ms 0 \
  --min-transactions 10000 \
  --max-transactions 15000
```

O padrao exige pelo menos 10.000 transacoes bem-sucedidas. Os resultados sao
gravados em `../reports/` nos formatos Markdown e JSON. Cada worker mantem uma
conexao MySQL persistente; abrir um cliente novo por transacao deve ser evitado
porque mede principalmente autenticacao e handshake TLS.

## Validacao e reversao

Antes da janela, registrar os valores atuais de `MAX(rental_id)` e
`MAX(payment_id)`. Depois da janela, confirmar que nao existem linhas
adicionais associadas as chaves capturadas pelo executor e confrontar as
metricas do Health Check.

Nao ha reversao funcional esperada porque cada transacao remove os proprios
dados sinteticos. Os saltos de `AUTO_INCREMENT` nao sao revertidos e devem ser
aceitos explicitamente ao autorizar a execucao.
