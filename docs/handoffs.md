# Handoffs entre agentes

Todo encaminhamento deve ser registrado em Markdown dentro da pasta de
requests, reports, evidence, analyses, proposals ou validations do agente
responsável. O registro deve apontar para arquivos reais; conteúdo comum é
referenciado por caminho e não duplicado.

## Campos obrigatórios

```markdown
# Handoff: <título>

- ID: <identificador>
- Data/hora: <ISO 8601 com fuso>
- Origem: <agente>
- Destino: <agente>
- Status: <novo|em análise|concluído|bloqueado>
- Escopo: <objetivo e limites>

## Evidências

- <caminho relativo ou ID autorizado>

## Cobertura e limitações

- <janela, fontes consultadas e o que não foi validado>

## Decisões e justificativas

- <decisão baseada em evidência>

## Riscos e limitações

- <risco, lacuna ou hipótese>

## Próximo passo

- <ação, responsável e critério de conclusão>
```

## Regras

- Usar caminhos relativos sempre que o artefato estiver no projeto.
- Não incluir segredo, evento bruto, conteúdo de perfil de login ou chave.
- Uma proposta de mudança no banco deve incluir validação, reversão e aprovação
  pendente ou registrada.
- O destinatário deve atualizar o mesmo handoff ou criar uma referência de
  retorno rastreável.
