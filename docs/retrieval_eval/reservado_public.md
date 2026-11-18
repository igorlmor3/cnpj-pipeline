# Avaliação da recuperação: conjunto reservado, público public

Gerado em 2026-11-18. Busca BM25 sobre a documentação do repositório (376 trechos; versão do corpus `71396f241f48`). Sem modelo e sem custo.

## Resumo

| Métrica | Valor |
|---|---|
| Perguntas com fonte neste público | 20 de 24 |
| Acerto nos 1 primeiros | 11 de 20 (55,0%; intervalo de 95%: 34,2% a 74,2%) |
| Acerto nos 3 primeiros | 14 de 20 (70,0%; intervalo de 95%: 48,1% a 85,5%) |
| Acerto nos 5 primeiros | 15 de 20 (75,0%; intervalo de 95%: 53,1% a 88,8%) |
| MRR@5 | 0,621 |
| Resultados de público não permitido | 0 |

## Por categoria (acerto nos 3 primeiros)

| Categoria | Acertos | Perguntas |
|---|---|---|
| decisao | 3 | 4 |
| definicao | 1 | 3 |
| infra | 2 | 2 |
| limite | 2 | 4 |
| numero | 0 | 1 |
| privacidade | 2 | 2 |
| regra | 2 | 2 |
| ressalva | 2 | 2 |

## Perguntas sem trecho relevante nos 3 primeiros

**r_camadas** (fora dos 5 primeiros): Quais são as camadas do pipeline e o que cada uma guarda?

- README.md, seção cnpj-pipeline > Resultados medidos > Agente de texto para SQL
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > O que a guarda barra hoje
- site/README.md, seção Site > Dados e configuração

**r_linhas_fato** (fora dos 5 primeiros): Quantas linhas tem a tabela fato da gold?

- docs/architecture.md, seção Arquitetura e decisões > Decisões em aberto
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Objetivo e limite
- README.md, seção cnpj-pipeline > Camada gold

**r_tempo_consulta** (posição 4): Quanto tempo uma consulta pode levar antes de ser cancelada?

- docs/architecture.md, seção Arquitetura e decisões > Decisões de plataforma e de produto
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Busca na documentação
- docs/analysis/2026-10.md, seção Análise do cadastro de CNPJ (mês de referência 2026-10) > 6. Sobrevivência por ano de abertura

**r_comparacao_meses** (fora dos 5 primeiros): Como é feita a comparação entre dois meses do cadastro?

- docs/analysis/evolucao_2026-07_a_2026-10.md, seção Evolução do cadastro de CNPJ entre julho e outubro de 2026
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Avaliação
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Avaliação > Segunda rodada: robustez e validação

**r_cnae_9492800** (fora dos 5 primeiros): O que é o CNAE 9492800?

- docs/analysis/2026-10.md, seção Análise do cadastro de CNPJ (mês de referência 2026-10) > Apêndice: consultas
- docs/analysis/2026-10.md, seção Análise do cadastro de CNPJ (mês de referência 2026-10) > 5. Setores que mais cresceram em aberturas (2019 para 2025)
- README.md, seção cnpj-pipeline > Camada gold

**r_tamanho_json** (fora dos 5 primeiros): Qual o tamanho máximo de cada arquivo JSON do site?

- docs/site_data.md, seção Dados do site
- site/README.md, seção Site > Dados e configuração
- docs/site_data.md, seção Dados do site > Arquivos

## Sem fonte neste público

A evidência dessas perguntas está só em documentos que este público não vê: r_chaves_iam, r_portao_qualidade, r_limite_athena, r_mes_novo_api.

## Como ler

- Um trecho é relevante se contém toda a evidência do caso, sem diferença de caixa nem de acento. Isso mede se a busca traz o trecho que responde, não se o modelo o usa bem.
- Com poucas perguntas o intervalo é largo; compare duas buscas pelas mesmas perguntas, não por intervalos que se sobrepõem.
- O conjunto `reservado` não é usado para ajustar a busca; o `dev` é.
