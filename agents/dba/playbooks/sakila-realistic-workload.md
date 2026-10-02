# Workload realista controlado do Sakila

## Objetivo

Exercitar um fluxo de locacao plausivel no schema `sakila` do MySQL HeatWave
gerenciado: criar aluguel e pagamento, consultar o fluxo, registrar devolucao
e depois remover apenas os registros sinteticos criados pelo teste. E uma
carga controlada, nao um teste de stress.

## Executor e alvo

- Executor: `../scripts/sakila_realistic_workload.py`
- Configuração central: `../../../config/agent-monitoring.toml`
- Login path: `workload_login_path` da configuração central
- Alvo configurado: `alvo definido no perfil central, schema `sakila``, TCP e TLS obrigatorios

O perfil e apenas referenciado; seu conteudo nunca deve ser aberto, copiado ou
incluido em logs e relatorios.

## Fluxo transacional

Cada worker usa uma sessao MySQL persistente e escolhe, por percentuais
editaveis (padrao 25/25/25/25), uma destas operacoes. O executor usa
agendamento por deficit: quando `UPDATE` ou `DELETE` ainda nao sao elegiveis,
ele acumula o deficit e os prioriza assim que houver registros na fila.

1. `READ`: consulta catalogo, clientes ou um aluguel criado pelo proprio teste.
2. `INSERT`: inicia transacao, reserva inventario livre, cria `rental` e
   `payment`, e confirma com `COMMIT`.
3. `UPDATE`: marca o aluguel sintetico como devolvido e ajusta seu pagamento,
   em transacao separada.
4. `DELETE`: apaga pagamento e aluguel sinteticos devolvidos, em outra
   transacao.

O executor nunca atualiza ou exclui linhas preexistentes. O cleanup final
remove registros sinteticos ainda pendentes. Saltos de `AUTO_INCREMENT` de
`rental` e `payment` permanecem e sao o unico efeito persistente esperado.

## Protecoes e parametros

- Sem `--execute`, nao ha conexao nem DML.
- A execucao exige tambem `--confirm-target sakila`.
- O preflight exige `USE sakila`, porta 3306, servidor nao read-only, ambiente
  identificado como Cloud/HeatWave e as seis tabelas do fluxo.
- Padrao: 8 workers, 600 segundos, minimo de 10.000 transacoes bem-sucedidas
  e limite absoluto de 15.000 tentativas.
- Se o minimo nao for alcancado ao fim dos 600 segundos, os workers continuam
  ate atingir 10.000 ou o limite de tentativas; o relatorio marca
  `minimum_met` explicitamente.
- Cada transacao tem timeout de 45 segundos; erros sao sanitizados no relatorio.
- Percentuais devem somar 100: `--read-percent`, `--insert-percent`,
  `--update-percent`, `--delete-percent`.

## Dry-run

```sh
python3 ../scripts/sakila_realistic_workload.py
```

## Execucao autorizada

```sh
python3 ../scripts/sakila_realistic_workload.py \
  --execute \
  --confirm-target sakila \
  --duration-seconds 600 \
  --workers 8 \
  --think-time-ms 0 \
  --min-transactions 10000 \
  --max-transactions 15000
```

Os resultados sao gravados em `../reports/` em Markdown e JSON. Antes de uma
janela, registrar os `MAX(rental_id)` e `MAX(payment_id)` atuais; depois,
confirmar que os registros sinteticos do executor foram removidos. O teste nao
reverte os valores de `AUTO_INCREMENT`.
