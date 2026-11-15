# Agente de perguntas sobre a gold (texto para SQL)

Status: implementados e testados a guarda de SQL (`sql_guard.py`), o contexto semântico (`context.py`), os executores para DuckDB local e Athena (`executor.py`), o cliente do modelo (`llm.py`), o controle de gasto (`budget.py`) e o laço do agente (`ask.py`), todos em `src/cnpj_pipeline/agent/`. A avaliação, a API publicada e a interface estão planejadas e descritas aqui.

## Objetivo e limite

Responder perguntas em português sobre o cadastro de CNPJ (por exemplo, "quantas empresas ativas são optantes do MEI em cada UF?") gerando SQL sobre a gold, executando no Athena e explicando o resultado, sempre mostrando o SQL usado. O agente é público, então o desenho parte do princípio de que o modelo de linguagem pode gerar qualquer SQL, inclusive por manipulação do texto da pergunta. A segurança não depende do que se pede ao modelo; depende do que o programa deixa executar.

## Decisões

1. **Só agregados.** O modelo só enxerga os 5 marts e as 7 dimensões. O fato por estabelecimento (uma linha por CNPJ) não está na lista, então nenhuma linha individual é acessível, mesmo com SQL arbitrário. Nenhuma coluna da gold tem razão social, nome fantasia, endereço, telefone, e-mail nem nome de sócio.
2. **O programa escolhe o mês, não o modelo.** Cada leitura de tabela é reescrita para uma subconsulta fixa em `reference_month = 'AAAA-MM'`. Por isso uma pergunta não varre meses que ninguém pediu, e o volume lido por consulta fica previsível.
3. **O SQL executado é o reescrito.** A guarda lê o SQL para uma árvore, valida, reescreve e gera o texto de novo. Comentários e sintaxe que a árvore não representa não chegam ao Athena. Nomes são resolvidos pelo escopo do analisador, e não por busca textual: uma CTE com o nome de uma tabela real, definida em outro ponto da consulta, não esconde a tabela real.
4. **Camadas de defesa independentes.** (a) A guarda de SQL. (b) O workgroup do Athena, que já cancela consultas que leiam mais que o limite configurado, mais um tempo máximo de espera com cancelamento no cliente. (c) Uma credencial própria para o agente, só com consulta nas tabelas da gold. (d) Limite de linhas, de tamanho do SQL e de tokens e perguntas por visitante. Hoje existem a (a), o tempo máximo com cancelamento da (b) e, da (d), os limites de linhas, de tamanho do SQL, de tamanho da pergunta, de chamadas ao modelo e de consultas por pergunta, e o teto de gasto; o limite de dados lidos do workgroup já vem da infraestrutura. Faltam a (c) e o limite por visitante, que dependem da API publicada.
5. **Modelo de linguagem atrás de uma interface.** O agente fala com um cliente pequeno (envia mensagens e uma ferramenta `run_sql`, recebe texto ou chamada de ferramenta). Os testes usam um cliente falso e não fazem chamadas de rede. A chave do provedor vem de variável de ambiente (`ANTHROPIC_API_KEY`) e nunca entra no repositório; o cliente não aceita a chave como argumento, para ela não passar por código, log ou resposta. Erros do provedor viram mensagens fixas, sem o corpo da resposta.
6. **Contexto semântico gerado do código.** O texto que descreve as tabelas e colunas é montado a partir de `gold_models.py`, para não divergir da gold. Ele inclui as ressalvas do cadastro, em especial o CNAE 9492800: nas aberturas por mês ou por seção, o agente deve ler com e sem `is_organizacao_politica`, ou avisar, porque os picos das eleições municipais não são atividade econômica (ver [análise](analysis/2026-10.md)).
7. **Avaliação offline.** Um conjunto de perguntas com o resultado esperado, executado contra a gold local em DuckDB, mede se o SQL gerado responde certo. Serve para comparar prompts e modelos sem gastar com o Athena, e para ter números reais no README em vez de impressões.

## O laço do agente e o gasto

O modelo recebe a pergunta e a ferramenta `run_sql`. Cada SQL que ele escreve passa pela guarda e é executado; se for recusado ou falhar, a mensagem de erro (segura, com a lista de tabelas permitidas) volta ao modelo como resultado de ferramenta com erro, e ele corrige e tenta de novo. Limites por pergunta: 300 caracteres, 5 chamadas ao modelo, 4 consultas, 1.024 tokens de saída por chamada, e ao modelo voltam no máximo 40 linhas de cada resultado.

O resultado é sempre um `Answer` com um estado, nunca uma exceção causada por visitante, modelo ou motor: `answered`, `out_of_scope`, `failed`, `budget`, `llm_error`, `invalid_question` ou `internal_error`. Se o modelo responder sem consultar nada, o visitante vê uma recusa fixa, e não o texto livre do modelo, para o agente não virar um chatbot genérico. Falhas inesperadas aparecem como mensagem genérica; o detalhe vai só para o log.

Modelo e gasto: o provedor escolhido é a API da Anthropic, com o `claude-haiku-5-5` e teto de US$ 5 por mês. O teto de verdade é o limite de gasto configurado na própria conta do provedor; o programa tem um segundo teto (`budget.py`) que para de chamar o modelo antes de chegar lá, e o preço por token é um parâmetro, porque tabelas de preço mudam. Com o preço de lista de out/2026 (US$ 0,10 por milhão de tokens de entrada e US$ 0,50 de saída, para prompts de até 100 mil tokens) e o prompt de cerca de 3 mil tokens, uma pergunta típica (duas chamadas) custa na ordem de US$ 0,001 e o pior caso do laço (cinco chamadas) fica abaixo de US$ 0,005. É uma estimativa: o número real vem da avaliação com o modelo.

## O que a guarda barra hoje

Mais de uma instrução; qualquer coisa que não seja SELECT (DDL, DML, SHOW, DESCRIBE, EXPLAIN, SET, USE, CALL); tabela fora da lista, incluindo o fato, camadas silver e bronze, `information_schema`, tabelas de sistema (`...$partitions`), outro banco e qualquer catálogo; funções de tabela, UNNEST e LATERAL; WITH RECURSIVE; junção sem condição (inclui vírgula, CROSS e NATURAL) e mais de 4 junções; SQL com mais de 4.000 caracteres. O limite de linhas (200) é acrescentado ou reduzido. Os testes (`tests/test_sql_guard.py`) cobrem cada regra, incluindo tentativas de esconder uma tabela real atrás de uma CTE e a execução do SQL reescrito num DuckDB com dois meses, para provar que só o mês escolhido é lido.

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
- **Um mês por vez.** Cada pergunta olha um mês de referência. Comparar meses fica para quando houver mais de um mês publicado.

## Ordem de implementação

1. Guarda de SQL (feito).
2. Contexto semântico gerado das colunas reais da gold e de `gold_models.py`; um teste falha se aparecer coluna sem glossa (feito).
3. Executor com duas pontas (DuckDB local e Athena), limite de linhas, tempo máximo com cancelamento e mensagens de erro sem caminhos, ARNs nem número de conta (feito).
4. Cliente do modelo de linguagem, laço de correção (devolvendo a mensagem da guarda ao modelo), controle de gasto e cliente falso para testes (feito).
5. Conjunto de avaliação e relatório de acerto.
6. Exportação dos agregados da gold para JSON (dados do site), só com contagens e o mês de referência.
7. Função da API do agente (credencial própria por papel IAM, limites por visitante e interruptor).
8. Site em React com a análise, o relatório de qualidade e o agente (com perguntas de exemplo pré-gravadas).
9. Abertura ao público, só depois de testar os limites e o interruptor.
