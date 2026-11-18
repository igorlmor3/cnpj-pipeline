# Avaliação da recuperação: conjunto dev, público public

Gerado em 2026-11-18. Busca BM25 sobre a documentação do repositório (386 trechos; versão do corpus `b35300422327`). Sem modelo e sem custo.

## Resumo

| Métrica | Valor |
|---|---|
| Perguntas com fonte neste público | 22 de 24 |
| Acerto nos 1 primeiros | 13 de 22 (59,1%; intervalo de 95%: 38,7% a 76,7%) |
| Acerto nos 3 primeiros | 16 de 22 (72,7%; intervalo de 95%: 51,8% a 86,8%) |
| Acerto nos 5 primeiros | 18 de 22 (81,8%; intervalo de 95%: 61,5% a 92,7%) |
| MRR@5 | 0,674 |
| Resultados de público não permitido | 0 |

## Por categoria (acerto nos 3 primeiros)

| Categoria | Acertos | Perguntas |
|---|---|---|
| decisao | 3 | 3 |
| definicao | 3 | 3 |
| limite | 3 | 4 |
| numero | 1 | 3 |
| privacidade | 2 | 3 |
| regra | 3 | 4 |
| ressalva | 1 | 2 |

## Perguntas sem trecho relevante nos 3 primeiros

**d_gold_sem_contato** (posição 4): A gold tem nomes, endereços ou telefones?

- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Decisões
- README.md, seção cnpj-pipeline > Camada gold
- docs/data_quality_rules.md, seção Catálogo de regras de qualidade de dados > Regras da camada silver

**d_digito_verificador** (posição 4): Quantos CNPJs tinham dígito verificador inválido no mês real?

- README.md, seção cnpj-pipeline > Resultados medidos > Verificações de qualidade e máquina
- docs/data_quality_rules.md, seção Catálogo de regras de qualidade de dados > Achados do mês real (2026-10)
- docs/quality/2026-10.md, seção Qualidade de dados e tempos (2026-10) > Verificações informativas

**d_tempo_silver** (fora dos 5 primeiros): Quanto tempo levou a camada silver de Estabelecimentos?

- README.md, seção cnpj-pipeline > Resultados medidos > Camada gold
- README.md, seção cnpj-pipeline > Resultados medidos
- README.md, seção cnpj-pipeline > Camada gold

**d_guarda_recusa** (fora dos 5 primeiros): O que a guarda de SQL recusa?

- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > O que a guarda barra hoje
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Testes de propriedade e suíte de injeção de prompt
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Testes de propriedade e suíte de injeção de prompt

**d_encoding** (fora dos 5 primeiros): Qual o encoding dos arquivos da Receita e como é tratado?

- docs/architecture.md, seção Arquitetura e decisões > Camadas
- README.md, seção cnpj-pipeline > Arquitetura planejada
- docs/data_quality_rules.md, seção Catálogo de regras de qualidade de dados > Premissas de leitura

**d_estabelecimento_nao_e_empresa** (fora dos 5 primeiros): Estabelecimento é a mesma coisa que empresa nos marts?

- README.md, seção cnpj-pipeline > Resultados medidos > Camada gold
- README.md, seção cnpj-pipeline > Camada gold
- README.md, seção cnpj-pipeline > Camada gold

## Sem fonte neste público

A evidência dessas perguntas está só em documentos que este público não vê: d_recursos_terraform, d_dag_sem_agenda.

## Como ler

- Um trecho é relevante se contém toda a evidência do caso, sem diferença de caixa nem de acento. Isso mede se a busca traz o trecho que responde, não se o modelo o usa bem.
- Com poucas perguntas o intervalo é largo; compare duas buscas pelas mesmas perguntas, não por intervalos que se sobrepõem.
- O conjunto `reservado` não é usado para ajustar a busca; o `dev` é.
