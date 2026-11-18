# Avaliação da recuperação: conjunto reservado, público all

Gerado em 2026-11-18. Busca BM25 sobre a documentação do repositório (407 trechos; versão do corpus `57e545317916`). Sem modelo e sem custo.

## Resumo

| Métrica | Valor |
|---|---|
| Perguntas com fonte neste público | 24 de 24 |
| Acerto nos 1 primeiros | 10 de 24 (41,7%; intervalo de 95%: 24,5% a 61,2%) |
| Acerto nos 3 primeiros | 17 de 24 (70,8%; intervalo de 95%: 50,8% a 85,1%) |
| Acerto nos 5 primeiros | 18 de 24 (75,0%; intervalo de 95%: 55,1% a 88,0%) |
| MRR@5 | 0,552 |
| Resultados de público não permitido | 0 |

## Por categoria (acerto nos 3 primeiros)

| Categoria | Acertos | Perguntas |
|---|---|---|
| decisao | 3 | 4 |
| definicao | 1 | 3 |
| infra | 5 | 6 |
| limite | 2 | 4 |
| numero | 0 | 1 |
| privacidade | 2 | 2 |
| regra | 2 | 2 |
| ressalva | 2 | 2 |

## Perguntas sem trecho relevante nos 3 primeiros

**r_camadas** (fora dos 5 primeiros): Quais são as camadas do pipeline e o que cada uma guarda?

- README.md, seção cnpj-pipeline > Resultados medidos > Agente de texto para SQL
- README.md, seção cnpj-pipeline > Qualidade de dados
- README.md, seção cnpj-pipeline > Roadmap

**r_linhas_fato** (fora dos 5 primeiros): Quantas linhas tem a tabela fato da gold?

- docs/architecture.md, seção Arquitetura e decisões > Decisões em aberto
- README.md, seção cnpj-pipeline > Camada gold
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Objetivo e limite

**r_tempo_consulta** (fora dos 5 primeiros): Quanto tempo uma consulta pode levar antes de ser cancelada?

- infra/README.md, seção Infraestrutura na AWS (nível 1) > API pública do agente > Observabilidade da API
- infra/README.md, seção Infraestrutura na AWS (nível 1)
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Busca na documentação

**r_comparacao_meses** (fora dos 5 primeiros): Como é feita a comparação entre dois meses do cadastro?

- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Avaliação
- docs/analysis/evolucao_2026-07_a_2026-10.md, seção Evolução do cadastro de CNPJ entre julho e outubro de 2026
- docs/analysis/evolucao_2026-07_a_2026-10.md, seção Evolução do cadastro de CNPJ entre julho e outubro de 2026 > 3. As saídas variam mais do que as entradas

**r_limite_athena** (posição 4): Qual o limite de dados lidos por consulta no workgroup do Athena?

- infra/README.md, seção Infraestrutura na AWS (nível 1) > Custos e cuidados
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Decisões
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Testes de propriedade e suíte de injeção de prompt

**r_cnae_9492800** (fora dos 5 primeiros): O que é o CNAE 9492800?

- docs/analysis/2026-10.md, seção Análise do cadastro de CNPJ (mês de referência 2026-10) > Apêndice: consultas
- docs/analysis/2026-10.md, seção Análise do cadastro de CNPJ (mês de referência 2026-10) > 5. Setores que mais cresceram em aberturas (2019 para 2025)
- README.md, seção cnpj-pipeline > Camada gold

**r_tamanho_json** (fora dos 5 primeiros): Qual o tamanho máximo de cada arquivo JSON do site?

- docs/site_data.md, seção Dados do site
- site/README.md, seção Site > Dados e configuração
- docs/site_data.md, seção Dados do site > Arquivos

## Como ler

- Um trecho é relevante se contém toda a evidência do caso, sem diferença de caixa nem de acento. Isso mede se a busca traz o trecho que responde, não se o modelo o usa bem.
- Com poucas perguntas o intervalo é largo; compare duas buscas pelas mesmas perguntas, não por intervalos que se sobrepõem.
- O conjunto `reservado` não é usado para ajustar a busca; o `dev` é.
