# Avaliação da recuperação: conjunto dev, público all

Gerado em 2026-11-18. Busca BM25 sobre a documentação do repositório (397 trechos; versão do corpus `66627e788b87`). Sem modelo e sem custo.

## Resumo

| Métrica | Valor |
|---|---|
| Perguntas com fonte neste público | 24 de 24 |
| Acerto nos 1 primeiros | 12 de 24 (50,0%; intervalo de 95%: 31,4% a 68,6%) |
| Acerto nos 3 primeiros | 16 de 24 (66,7%; intervalo de 95%: 46,7% a 82,0%) |
| Acerto nos 5 primeiros | 18 de 24 (75,0%; intervalo de 95%: 55,1% a 88,0%) |
| MRR@5 | 0,595 |
| Resultados de público não permitido | 0 |

## Por categoria (acerto nos 3 primeiros)

| Categoria | Acertos | Perguntas |
|---|---|---|
| decisao | 3 | 3 |
| definicao | 1 | 3 |
| infra | 1 | 2 |
| limite | 3 | 4 |
| numero | 1 | 3 |
| privacidade | 3 | 3 |
| regra | 3 | 4 |
| ressalva | 1 | 2 |

## Perguntas sem trecho relevante nos 3 primeiros

**d_cnpj_zero** (fora dos 5 primeiros): O que significa o cnpj_basico 00000000?

- docs/profiling/2026-10/empresas.md, seção Profiling: Empresas (2026-10) > Detalhes > `cnpj_basico`
- docs/profiling/2026-10/estabelecimentos.md, seção Profiling: Estabelecimentos (2026-10) > Detalhes > `cnpj_basico`
- docs/profiling/2026-10/simples.md, seção Profiling: Simples (2026-10) > Detalhes > `cnpj_basico`

**d_digito_verificador** (posição 5): Quantos CNPJs tinham dígito verificador inválido no mês real?

- README.md, seção cnpj-pipeline > Resultados medidos > Verificações de qualidade e máquina
- docs/data_quality_rules.md, seção Catálogo de regras de qualidade de dados > Achados do mês real (2026-10)
- docs/quality/2026-10.md, seção Qualidade de dados e tempos (2026-10) > Verificações informativas

**d_exclusao_simples_zerada** (posição 4): O que significam as datas de exclusão do Simples zeradas?

- docs/profiling/2026-10/simples.md, seção Profiling: Simples (2026-10) > Detalhes > `data_exclusao_simples`
- docs/profiling/2026-10/simples.md, seção Profiling: Simples (2026-10) > Detalhes > `data_exclusao_mei`
- docs/silver/2026-10.md, seção Camada silver: regras aplicadas (2026-10) > Simples

**d_tempo_silver** (fora dos 5 primeiros): Quanto tempo levou a camada silver de Estabelecimentos?

- README.md, seção cnpj-pipeline > Resultados medidos > Camada gold
- README.md, seção cnpj-pipeline > Resultados medidos
- README.md, seção cnpj-pipeline > Camada silver

**d_guarda_recusa** (fora dos 5 primeiros): O que a guarda de SQL recusa?

- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > O que a guarda barra hoje
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > Testes de propriedade e suíte de injeção de prompt
- docs/agent_design.md, seção Agente de perguntas sobre a gold (texto para SQL) > O laço do agente e o gasto

**d_encoding** (fora dos 5 primeiros): Qual o encoding dos arquivos da Receita e como é tratado?

- docs/architecture.md, seção Arquitetura e decisões > Camadas
- README.md, seção cnpj-pipeline > Arquitetura planejada
- docs/data_quality_rules.md, seção Catálogo de regras de qualidade de dados > Premissas de leitura

**d_recursos_terraform** (fora dos 5 primeiros): Que recursos o Terraform cria na AWS?

- infra/README.md, seção Infraestrutura na AWS (nível 1) > Passo a passo
- README.md, seção cnpj-pipeline > Resultados medidos > Publicação da gold na AWS
- infra/README.md, seção Infraestrutura na AWS (nível 1) > API pública do agente > Observabilidade da API

**d_estabelecimento_nao_e_empresa** (fora dos 5 primeiros): Estabelecimento é a mesma coisa que empresa nos marts?

- README.md, seção cnpj-pipeline > Resultados medidos > Camada gold
- README.md, seção cnpj-pipeline > Camada gold
- README.md, seção cnpj-pipeline > Camada gold

## Como ler

- Um trecho é relevante se contém toda a evidência do caso, sem diferença de caixa nem de acento. Isso mede se a busca traz o trecho que responde, não se o modelo o usa bem.
- Com poucas perguntas o intervalo é largo; compare duas buscas pelas mesmas perguntas, não por intervalos que se sobrepõem.
- O conjunto `reservado` não é usado para ajustar a busca; o `dev` é.
