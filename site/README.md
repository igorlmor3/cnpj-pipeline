# Site

Página pública do projeto, em React com Tailwind, no estilo de catálogo: destaque no topo e fileiras que rolam na horizontal. Ela só lê os arquivos JSON agregados gerados por `cnpj_pipeline.site_export` (veja [docs/site_data.md](../docs/site_data.md)) e, opcionalmente, chama a API pública do agente. Não há servidor próprio: o resultado do build é estático.

## O que a página mostra

- Números gerais do cadastro, Top 10 de estados, Simples e MEI por UF.
- Aberturas por ano, por mês, por seção da CNAE e por UF, com as organizações políticas separadas.
- Perfil por porte e natureza jurídica, capital social mediano, municípios e sócios (apenas contagens).
- O que são Simples e MEI: definição de cada termo, como eles se sobrepõem e os percentuais do mês.
- Como foi feito: oito etapas, do download à API, com os números reais de cada uma, as decisões de engenharia e links para a documentação.
- O pipeline em números: linhas por camada, verificações de qualidade e verificações da gold.
- O agente: respostas gravadas com o modelo real e uma caixa de perguntas ao vivo, que chama a API.
- As ressalvas de cada arquivo, reunidas em "Antes de interpretar".

Cada gráfico tem uma tabela equivalente (botão "Tabela"), tooltip com navegação por teclado nos gráficos de linha e cores validadas contra o fundo escuro. O último ano e o último mês aparecem tracejados, porque ainda estão incompletos.

## Como rodar

Requer Node 22 ou mais novo.

```
cd site
npm install
npm run dev
```

O servidor de desenvolvimento abre em `http://localhost:5173/cnpj-pipeline/`. A origem `http://localhost:5173` já está liberada no CORS da API.

```
npm test          # Vitest: formatação, escalas, leitor de markdown, API, carregamento dos dados reais, gráficos e páginas
npm run build     # verificação de tipos e build em dist/
npm run preview   # serve o build local
```

Para publicar em outro endereço, defina `BASE_PATH` no build (por exemplo `BASE_PATH=/ npm run build`).

## Dados e configuração

- `public/data/months.json` aponta o mês mais recente; `public/data/<mês>/` tem um JSON por tema, mais o `index.json` com o tamanho e o SHA-256 de cada arquivo. Eles são gerados por `python -m cnpj_pipeline.site_export` e não são editados à mão.
- `public/data/<mês>/agent_examples.json` guarda as respostas gravadas do agente (`python -m cnpj_pipeline.agent.record_examples`). Se o arquivo não existir, a fileira de exemplos não aparece.
- `public/config.json` tem o endereço público da API e o do repositório. É um arquivo público e não contém chaves. Sem `apiUrl`, a caixa de perguntas ao vivo não aparece.

A página valida o esquema e o mês de cada arquivo ao carregar; se algo não bate, mostra uma mensagem em vez de números errados.

## Respostas da API

A caixa ao vivo trata cada resposta da API: resposta com consulta, recusa de escopo, agente desligado ou sem orçamento, limite de uso e erro do servidor. O texto do agente é lido como dado (parágrafos, tabelas e código) e nunca vira HTML.

## Publicação

O workflow `.github/workflows/pages.yml` roda os testes, gera o build e publica no GitHub Pages a cada push em `main` que altera `site/`. No repositório, em Settings > Pages, a fonte deve ser "GitHub Actions". O endereço é `https://igorlmor3.github.io/cnpj-pipeline/`.
