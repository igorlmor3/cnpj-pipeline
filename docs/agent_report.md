# Agente de perguntas sobre o CNPJ: relatório de uma página

O agente responde perguntas em português sobre o cadastro de CNPJ escrevendo SQL (dialeto Trino) sobre os marts agregados da gold, executando e explicando o resultado, sempre mostrando o SQL. Modelo `claude-haiku-5-5`, mês 2026-10, gold real. O detalhe do método está em [agent_design.md](agent_design.md); os arquivos de cada execução, em [agent_eval/](agent_eval/).

## Como é medido

Cada pergunta de dados traz uma consulta de referência, executada na mesma gold e pela mesma guarda do agente. A resposta conta como certa se o resultado da última consulta do modelo bate com o da referência (acerto por execução, não comparação de texto). Nas perguntas de recusa, o certo é terminar sem resposta baseada em consulta; nas de ressalva (organizações políticas), o SQL precisa tê-la considerado. Os conjuntos são escritos com as referências conferidas antes de qualquer chamada ao modelo. Um conjunto só vale como reservado enquanto ninguém ajusta o prompt olhando para as suas falhas. Com repetições, o intervalo de 95% reamostra as perguntas, não as execuções, porque repetir uma pergunta mede a estabilidade do modelo e não cobre mais tipos de pergunta.

## Resultados (prompt `0d59d2bd1cf7`)

| Conjunto | Papel | Perguntas | Execuções certas | Intervalo de 95% |
|---|---|---:|---:|---|
| `holdout`, 5 repetições | reservado: nunca foi usado para ajustar o prompt | 24 | 119 de 120 (99,2%) | 97,5% a 100% |
| `validacao`, 3 repetições | desenvolvimento (suas falhas foram lidas) | 25 | 73 de 75 (97,3%) | 93,3% a 100% |
| `robustez` | desenvolvimento | 32 | 31 de 32 (96,9%) | 84,3% a 99,4% |
| `dev`, 5 repetições | desenvolvimento (o prompt partiu dele) | 26 | 130 de 130 (100%) | otimista por construção |
| `meses` (4 meses publicados) | cobre perguntas com vários meses | 6 | 6 de 6 | 61,0% a 100% |

Só o `holdout` é um número que o ajuste não tocou, e ele é pequeno: 24 perguntas, um mês, um modelo. Houve 4 execuções erradas, de causas diferentes: uma recusa indevida (descrição da seção G do CNAE, no `holdout`, 1 de 5 repetições), um filtro por `LIKE` que pegou uma qualificação a mais (sócio-administrador, no `robustez`) e duas no `validacao` (soma de MEI em MG e PR; filtro por Goiânia) em que o valor esperado apareceu junto com outras linhas. Das 75 perguntas rodadas com repetições (`dev`, `holdout` e `validacao`), 3 foram instáveis e as outras 72 acertaram em todas as repetições.

## Custo e latência

Por pergunta, média de 1,8 chamadas ao modelo e cerca de US$ 0,0011 a 0,0014 (p95 até US$ 0,0018), com 9 a 11 mil tokens de entrada e 0,4 a 0,6 mil de saída. Latência mediana de 2,8 a 3,5 s e p95 de 4,3 a 7,4 s. As cinco execuções acima somam US$ 0,43. O teto é de US$ 5 por mês no provedor e um segundo teto no programa.

## Segurança

A segurança não depende do modelo: o modelo só vê marts e dimensões agregados, e a guarda (sqlglot) reescreve cada consulta, fixa os meses, limita funções, junções e linhas. Testes de propriedade com Hypothesis (inclusive uma comparação do resultado com e sem os dados que o modelo não pode ler) e uma suíte de injeção que assume um modelo que obedece a qualquer ordem (74 ataques recusados, 7 aceitos por parecerem inofensivos e neutralizados, 10 atravessando o laço do agente) acharam e corrigiram 4 falhas reais da guarda. A busca na documentação (BM25, sem custo por consulta) filtra por público antes de pontuar: acerto nos 5 primeiros de 81,8% (`dev`) e 75,0% (`reservado`) nas perguntas com fonte pública, sem nenhum resultado de público não permitido.

## O que impede de levar além

- Conjuntos de 24 a 32 perguntas: um acerto a mais ou a menos muda 3 a 4 pontos, e os intervalos são largos.
- O `reservado2` (25 perguntas) está escrito e validado, mas ainda não foi rodado com o modelo; portanto não há hoje número reservado para o prompt atual além do `holdout`.
- Um único modelo e um único mês nas perguntas de um mês só; não há comparação entre modelos.
- A verificação de fidelidade dos números do texto existe e está na API, mas as execuções acima são anteriores a ela e não a medem.
- O rótulo de cada grupo não é conferido nas perguntas com vários grupos; uma resposta errada com SQL válido é possível, e por isso a resposta sempre mostra o SQL.
- Os testes da guarda rodam em sqlglot e DuckDB, não no Athena, e uma condição de junção sempre verdadeira que cite colunas passa (a defesa são o limite de 4 junções, o tempo máximo e o limite de dados lidos do workgroup).
- A avaliação com o modelo não roda no CI. O CI confere sem custo as referências, o hash do prompt, as perguntas e as consultas medidas contra a base [baseline.json](agent_eval/baseline.json), e a comparação com uma execução nova só reprova uma queda maior do que o acaso explicaria.
