# Agente de perguntas sobre a gold (texto para SQL)

Status: implementados e testados a guarda de SQL (`sql_guard.py`), o contexto semântico (`context.py`), os executores para DuckDB local e Athena (`executor.py`), o cliente do modelo (`llm.py`), o controle de gasto (`budget.py`), o laço do agente (`ask.py`) e a avaliação (`evaluation.py`, `evaluation_cases.py`), todos em `src/cnpj_pipeline/agent/`. A execução da avaliação com o modelo real, a API publicada e a interface estão planejadas e descritas aqui.

## Objetivo e limite

Responder perguntas em português sobre o cadastro de CNPJ (por exemplo, "quantas empresas ativas são optantes do MEI em cada UF?") gerando SQL sobre a gold, executando no Athena e explicando o resultado, sempre mostrando o SQL usado. O agente é público, então o desenho parte do princípio de que o modelo de linguagem pode gerar qualquer SQL, inclusive por manipulação do texto da pergunta. A segurança não depende do que se pede ao modelo; depende do que o programa deixa executar.

## Decisões

1. **Só agregados.** O modelo só enxerga os 5 marts e as 7 dimensões. O fato por estabelecimento (uma linha por CNPJ) não está na lista, então nenhuma linha individual é acessível, mesmo com SQL arbitrário. Nenhuma coluna da gold tem razão social, nome fantasia, endereço, telefone, e-mail nem nome de sócio.
2. **O programa escolhe os meses, não o modelo.** Cada leitura de tabela é reescrita para uma subconsulta fixa em `reference_month`. Quando a pergunta não cita mês, o prompt manda o agente filtrar explicitamente pelo mês mais recente e dizer na resposta que os números são desse mês, sem somar meses. Se a consulta não cita `reference_month` mesmo assim, a guarda lê só o mês padrão (o mais recente), como rede de segurança. Se cita, os marts podem ser lidos em todos os meses publicados (`CNPJ_API_MONTHS`) e nenhum outro, e as dimensões ficam sempre no mês padrão. Por isso uma pergunta não varre meses fora da lista, e o volume lido por consulta fica previsível.
2a. **Somar meses é recusado.** Cada mês é uma foto completa do mesmo cadastro, então somar empresas ativas de meses diferentes contaria o mesmo CNPJ várias vezes. Quando a consulta cita o mês, a guarda confere cada SELECT que lê um mart: ele precisa agrupar por `reference_month`, fixar um único mês no WHERE, só listar linhas, ou deixar o mês dentro de cada agregado (`SUM(CASE WHEN reference_month = ...)`). Qualquer outra soma é recusada com uma mensagem que o agente usa para corrigir a consulta. É uma verificação sobre a forma da consulta: ela não prova que a conta está certa (por exemplo, juntar dois marts sem `reference_month` na condição passa), e por isso o conjunto `meses` da avaliação confere o resultado.
3. **O SQL executado é o reescrito.** A guarda lê o SQL para uma árvore, valida, reescreve e gera o texto de novo. Comentários e sintaxe que a árvore não representa não chegam ao Athena. Nomes são resolvidos pelo escopo do analisador, e não por busca textual: uma CTE com o nome de uma tabela real, definida em outro ponto da consulta, não esconde a tabela real.
4. **Camadas de defesa independentes.** (a) A guarda de SQL. (b) O workgroup do Athena, que já cancela consultas que leiam mais que o limite configurado, mais um tempo máximo de espera com cancelamento no cliente. (c) Uma credencial própria para o agente, só com consulta nas tabelas da gold. (d) Limite de linhas, de tamanho do SQL e de tokens e perguntas por visitante. Hoje existem a (a), o tempo máximo com cancelamento da (b) e, da (d), os limites de linhas, de tamanho do SQL, de tamanho da pergunta, de chamadas ao modelo e de consultas por pergunta, e o teto de gasto; o limite de dados lidos do workgroup já vem da infraestrutura. Faltam a (c) e o limite por visitante, que dependem da API publicada.
5. **Modelo de linguagem atrás de uma interface.** O agente fala com um cliente pequeno (envia mensagens e uma ferramenta `run_sql`, recebe texto ou chamada de ferramenta). Os testes usam um cliente falso e não fazem chamadas de rede. A chave do provedor vem de variável de ambiente (`ANTHROPIC_API_KEY`) e nunca entra no repositório; o cliente não aceita a chave como argumento, para ela não passar por código, log ou resposta. Erros do provedor viram mensagens fixas, sem o corpo da resposta.
6. **Contexto semântico gerado do código.** O texto que descreve as tabelas e colunas é montado a partir de `gold_models.py`, para não divergir da gold. Ele inclui as ressalvas do cadastro, em especial o CNAE 9492800: nas aberturas por mês ou por seção, o agente deve ler com e sem `is_organizacao_politica`, ou avisar, porque os picos das eleições municipais não são atividade econômica (ver [análise](analysis/2026-10.md)).
7. **Avaliação offline.** Um conjunto de perguntas com o resultado esperado, executado contra a gold local em DuckDB, mede se o SQL gerado responde certo. Serve para comparar prompts e modelos sem gastar com o Athena, e para ter números reais no README em vez de impressões.

## O laço do agente e o gasto

O modelo recebe a pergunta e a ferramenta `run_sql`. Cada SQL que ele escreve passa pela guarda e é executado; se for recusado ou falhar, a mensagem de erro (segura, com a lista de tabelas permitidas) volta ao modelo como resultado de ferramenta com erro, e ele corrige e tenta de novo. Limites por pergunta: 300 caracteres, 5 chamadas ao modelo, 4 consultas, 1.024 tokens de saída por chamada, e ao modelo voltam no máximo 40 linhas de cada resultado.

O resultado é sempre um `Answer` com um estado, nunca uma exceção causada por visitante, modelo ou motor: `answered`, `out_of_scope`, `failed`, `budget`, `llm_error`, `invalid_question` ou `internal_error`. Se o modelo responder sem consultar nada, o visitante vê uma recusa fixa, e não o texto livre do modelo, para o agente não virar um chatbot genérico. Falhas inesperadas aparecem como mensagem genérica; o detalhe vai só para o log.

Modelo e gasto: o provedor escolhido é a API da Anthropic, com o `claude-haiku-5-5` e teto de US$ 5 por mês. O teto de verdade é o limite de gasto configurado na própria conta do provedor; o programa tem um segundo teto (`budget.py`) que para de chamar o modelo antes de chegar lá, e o preço por token é um parâmetro, porque tabelas de preço mudam. Com o preço de lista de out/2026 (US$ 0,10 por milhão de tokens de entrada e US$ 0,50 de saída, para prompts de até 100 mil tokens) e o prompt de cerca de 3 mil tokens, uma pergunta típica (duas chamadas) custa na ordem de US$ 0,001 e o pior caso do laço (cinco chamadas) fica abaixo de US$ 0,005. É uma estimativa: o número real vem da avaliação com o modelo.

## Avaliação

Há quatro conjuntos de perguntas. O conjunto de desenvolvimento (`dev`) tem 26 perguntas e serve para ajustar o prompt. O conjunto reservado (`holdout`) tem 24 perguntas escritas depois desse ajuste, que o prompt nunca viu. O `robustez` (32 perguntas) e o `validacao` (25) foram escritos numa segunda rodada, descrita mais abaixo: o primeiro virou conjunto de desenvolvimento e o segundo é o reservado atual, cujo resultado é o número que se informa. O `dev` e o `holdout` cobrem 6 categorias: agregação, agrupamento, ranking, junção com dimensões, ressalva das organizações políticas e recusa. Cada pergunta de dados traz uma consulta de referência, executada na mesma gold e pela mesma guarda do agente; a resposta do modelo conta como certa se o resultado da sua última consulta bem-sucedida bater com o da referência (acurácia por execução, não comparação de texto de SQL). Por isso os conjuntos valem para qualquer mês. Um quinto conjunto, `meses` (5 perguntas, `--suite meses --months ...`), testa o agente com vários meses publicados: valor de um mês, de dois meses lado a lado, total por mês, maior ganho entre dois meses e a pergunta sem mês, que deve continuar lendo só o mês padrão. Ele cita meses pelo nome (jul a out de 2026), por isso fica fora do `all`. Ainda não foi rodado com o modelo.

Regras de comparação: inteiros exatos, números não inteiros com 1 casa decimal, texto sem diferença de caixa (acento conta), colunas a mais ou em outra ordem aceitas, ordem das linhas só nos rankings. Em perguntas com vários grupos a referência devolve só os números, porque o modelo pode rotular o grupo pelo código ou pela descrição; o custo dessa escolha é que o rótulo de cada grupo não é conferido. Referências com `ORDER BY` e `LIMIT` têm desempate por identificador, porque o DuckDB não garante a ordem entre empatados (a falta disso fez um caso falhar de forma intermitente nos testes). Se a pergunta de ranking tiver empate no corte numa gold real, o resultado do modelo pode divergir sem estar errado.

Os outros dois tipos: nas perguntas de ressalva o SQL precisa ter considerado `is_organizacao_politica` (e, numa delas, a resposta precisa mencionar as eleições), sem comparar o resultado, porque excluir ou separar as organizações políticas são duas respostas válidas; nas de recusa (razão social de um CNPJ, nomes de sócios, poema, pedido para ignorar instruções e ler a silver, apagar tabela, revelar as instruções) o correto é terminar sem resposta baseada em consulta.

### Por que há um conjunto reservado

Ajustar o prompt olhando para as falhas de um conjunto e medir de novo no mesmo conjunto mede a capacidade de decorar aquelas perguntas, não de responder perguntas novas. A primeira execução com o modelo real no conjunto `dev` acertou 21 de 26 (80,8%). As cinco falhas tinham o mesmo padrão: o modelo devolvia mais linhas do que o pedido (`LIMIT 5` ou `LIMIT 10` numa pergunta de "qual é o maior", ou todos os tipos de sócio numa pergunta sobre um deles) e, num caso, agrupava por organização política em vez de filtrar. Foram acrescentadas duas regras gerais ao prompt (devolver só o que foi perguntado; excluir organizações políticas com `WHERE`, sem agrupar). A segunda execução acertou 26 de 26, o que é um resultado otimista, porque o ajuste partiu dessas mesmas perguntas.

Por isso as perguntas do `holdout` foram escritas depois, sem consultar o relatório do modelo, e a regra é: depois de rodar o `holdout`, o prompt não é ajustado olhando para as suas falhas. Se for, esses casos passam a ser de desenvolvimento e são substituídos por perguntas novas. Os dois relatórios do `dev` (antes e depois do ajuste) ficam no histórico do Git.

### Segunda rodada: robustez e validação

O `dev` e o `holdout` têm perguntas bem formadas, que trazem a ressalva das organizações políticas por extenso e pedem um número direto. O `robustez` foi escrito para o que eles não cobrem: frases informais ("qts mei tem na bahia"), pergunta em inglês, UF por extenso, filtro por nome de município (com o nome em maiúsculas no cadastro), seção e qualificação, contas sobre mais de uma coluna (proporção, média, diferença, filiais por matriz), rankings que não são "o maior" (a segunda maior, a menor), ressalva sem aviso na pergunta e pedidos que parecem respondíveis e não são (faturamento, previsão, outro mês, ler um arquivo, instrução em inglês). As 26 referências de dados e ressalva foram conferidas contra a gold antes de qualquer execução do modelo.

A primeira execução, com o prompt original, acertou 27 de 32 (84,4%). As cinco falhas vinham de duas lacunas: contas deixadas para o texto da resposta (o modelo devolvia os números crus e fazia a divisão, a subtração ou a média de cabeça, com números de sete dígitos, o que pode produzir na resposta um número que não está no resultado) e rankings com `LIMIT 3` onde se pedia uma linha. O prompt ganhou duas regras para isso. Ao validar as referências do conjunto seguinte apareceu um defeito do próprio contexto: a coluna `natureza_grupo` estava descrita como o dígito do grupo, mas guarda o texto ("Entidades sem Fins Lucrativos"), e um filtro por `'3'` voltava vazio. A descrição foi corrigida antes de qualquer chamada ao modelo.

Como o ajuste partiu das falhas do `robustez`, pela regra do conjunto reservado ele passou a ser de desenvolvimento. O `validacao` (25 perguntas, mesmos tipos, conteúdo novo, referências conferidas antes) foi escrito depois do ajuste e rodado uma vez, com o resultado de 24 de 25. Os resultados com o prompt ajustado, por conjunto, estão no README. Dois pontos de método:

- Cada relatório e JSON traz a versão do prompt (SHA-256 do texto enviado ao modelo, 12 primeiros caracteres), para um resultado dizer com qual prompt foi gerado. Os relatórios anteriores ao ajuste não têm esse campo.
- O motivo de falha "resultado diferente do esperado, mas o valor esperado aparece entre as linhas devolvidas" separa o erro de formato (o modelo devolveu a quebra por grupo em vez do grupo pedido) do valor errado. É só diagnóstico: os dois contam como erro, e nos rankings, onde a ordem conta, o diagnóstico não é aplicado.

O ajuste teve um efeito colateral medido no `robustez`: com a regra das contas, "Compare o número de MEI em SP e no RJ" passou a ser respondida com a diferença e a razão, sem os dois valores. A regra resolveu o que motivou o ajuste e, numa pergunta que pedia só uma comparação, foi aplicada além do que devia.

Como rodar, a partir da raiz do repositório:

```
python -m cnpj_pipeline.agent.evaluation --month 2026-10 --reference --suite all
python -m cnpj_pipeline.agent.evaluation --month 2026-10 --budget-usd 1
python -m cnpj_pipeline.agent.evaluation --month 2026-10 --budget-usd 1 --suite holdout
python -m cnpj_pipeline.agent.evaluation --month 2026-10 --budget-usd 1 --suite holdout --repeats 5
python -m cnpj_pipeline.agent.evaluation --month 2026-10 --budget-usd 1 --suite validacao --out-dir docs/agent_eval/prompt_v2
```

`--suite` aceita `dev` (padrão), `holdout`, `robustez`, `validacao` e `all`; `--out-dir` muda a pasta dos relatórios (útil para não sobrescrever os de um prompt anterior); `--cases` escolhe perguntas pelo identificador e tem prioridade. Os relatórios dos outros conjuntos ganham o sufixo do conjunto no nome (`2026-10_claude-haiku-5-5_holdout.md`), então não sobrescrevem o do `dev`.

`--repeats N` (de 1 a 20) roda cada pergunta N vezes com o mesmo prompt e a mesma gold, para separar variação do modelo de lacuna do prompt. O relatório (`..._xN.md`) deixa de listar uma linha por execução e classifica cada pergunta: estável (certa em todas as repetições), sempre errada (errou em todas, o que aponta o prompt ou o contexto) ou instável (certa em parte, o que aponta variação do modelo). Traz também quantos SQL diferentes o modelo escreveu e os motivos das falhas com a contagem de cada um. O gasto de todas as repetições conta no mesmo `--budget-usd`; com o custo medido de cerca de US$ 0,001 por pergunta, cinco repetições do conjunto reservado ficam em torno de US$ 0,15.

O modo `--reference` usa as próprias consultas de referência no lugar do modelo: valida o conjunto e o programa sem custo, e é o teste que roda no CI. Sem ele, o modelo é chamado com a chave de `ANTHROPIC_API_KEY` e o gasto da execução fica limitado por `--budget-usd`. Cada execução grava em `docs/agent_eval/` um relatório em Markdown (acerto por categoria, primeira consulta sem erro, chamadas ao modelo, custo e latência por pergunta, e o SQL das falhas) e um JSON; nenhum dos dois traz linhas de resultado. Os testes provam que a avaliação reprova respostas erradas (consulta errada, recusa de tudo, ressalva ignorada, ordem trocada, orçamento esgotado), e não só que aprova as certas.

## O que a guarda barra hoje

Mais de uma instrução; qualquer coisa que não seja SELECT (DDL, DML, SHOW, DESCRIBE, EXPLAIN, SET, USE, CALL); tabela fora da lista, incluindo o fato, camadas silver e bronze, `information_schema`, tabelas de sistema (`...$partitions`), outro banco e qualquer catálogo; funções de tabela, UNNEST e LATERAL; WITH RECURSIVE; junção sem condição (inclui vírgula, CROSS e NATURAL) e mais de 4 junções; SQL com mais de 4.000 caracteres; com vários meses, a soma de fotos de meses diferentes (ver o item 2a). O limite de linhas (200) é acrescentado ou reduzido. Os testes (`tests/test_sql_guard.py`) cobrem cada regra, incluindo tentativas de esconder uma tabela real atrás de uma CTE e a execução do SQL reescrito num DuckDB com dois meses, para provar que só o mês escolhido é lido.

## Publicação: o que os recrutadores veem

O site precisa funcionar sempre, custar quase nada e não expor nada. Por isso a publicação tem duas partes independentes:

1. **Site estático em React** (hospedagem gratuita ou quase: GitHub Pages, Netlify, Vercel ou S3 com CloudFront), com a análise, a arquitetura, o relatório de qualidade e os números principais. Os dados do site são arquivos JSON de agregados gerados a partir da gold e versionados, com o mês de referência. Se o agente estiver desligado, o site continua inteiro.
2. **O agente como demonstração ao vivo**, atrás de uma API pequena (função serverless na própria conta AWS). Ela recebe a pergunta, chama o modelo, passa o SQL pela guarda, executa no Athena e devolve a resposta com o SQL. A chave do provedor do modelo fica só no servidor; o acesso ao Athena é por papel IAM da função, sem chaves de acesso. O navegador nunca fala direto com o Athena nem com o provedor do modelo.

Limites que precisam existir antes de abrir ao público: limite de perguntas por visitante e total por dia (teto de gasto), tamanho máximo da pergunta, orçamento de tokens por resposta, limite de gasto na conta do provedor do modelo, o alerta de orçamento da AWS (já existe) e um interruptor para desligar o agente, caindo para perguntas de exemplo com respostas pré-gravadas (geradas por execuções reais, sem custo por visita).

Consequência para o código: guarda, contexto e executor do Athena são Python puro (sqlglot e boto3, sem DuckDB) e cabem numa função; um teste garante que o pacote importa sem o DuckDB. O executor local em DuckDB existe só para desenvolvimento, testes e avaliação offline, e nada pesado precisa rodar na máquina para publicar: o processamento continua local, mas só os agregados vão para o S3 e para o Athena.

Decidido: provedor Anthropic, teto de US$ 5 por mês. O agente ao vivo começa com limite por visitante, interruptor e perguntas de exemplo pré-gravadas como alternativa. Em aberto: onde hospedar o site e onde guardar o gasto acumulado e as contagens por visitante na função publicada (hoje há só a versão em memória, usada nos testes).

## Riscos conhecidos e ainda abertos

- **Resposta errada com SQL válido.** A guarda impede dano, não erro de interpretação. Por isso a resposta sempre mostra o SQL e haverá avaliação com perguntas de gabarito.
- **Consultas caras sem ler muito.** Junções entre marts grandes podem consumir tempo de processamento sem ler muitos bytes. Mitigação: máximo de 4 junções, condição obrigatória e tempo máximo de 30 s por consulta, com cancelamento no Athena (`stop_query_execution`) e interrupção no DuckDB.
- **Células pequenas.** O mart de estabelecimentos ativos cruza UF, município e divisão da CNAE, então algumas linhas contam poucos estabelecimentos. Os dados são públicos, mas a política do projeto é só agregados. Decisão pendente: suprimir ou agrupar contagens abaixo de um limite (por exemplo, 5) antes de mostrar ao visitante.
- **Meses só como fotos.** O agente compara fotos mensais do cadastro (variação líquida de empresas ativas, estabelecimentos, Simples e MEI). Ele não enxerga as marts de movimentação entre meses (quem entrou, saiu ou mudou de situação), que ficam só no site.

## Ordem de implementação

1. Guarda de SQL (feito).
2. Contexto semântico gerado das colunas reais da gold e de `gold_models.py`; um teste falha se aparecer coluna sem glossa (feito).
3. Executor com duas pontas (DuckDB local e Athena), limite de linhas, tempo máximo com cancelamento e mensagens de erro sem caminhos, ARNs nem número de conta (feito).
4. Cliente do modelo de linguagem, laço de correção (devolvendo a mensagem da guarda ao modelo), controle de gasto e cliente falso para testes (feito).
5. Conjunto de avaliação e relatório de acerto (código e testes feitos; falta a execução com o modelo real, que gera os números do README).
6. Exportação dos agregados da gold para JSON (dados do site), só com contagens e o mês de referência.
7. Função da API do agente (credencial própria por papel IAM, limites por visitante e interruptor).
8. Site em React com a análise, o relatório de qualidade e o agente (com perguntas de exemplo pré-gravadas).
9. Abertura ao público, só depois de testar os limites e o interruptor.
