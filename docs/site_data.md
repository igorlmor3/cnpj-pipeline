# Dados do site

O site é estático e precisa funcionar sem a AWS e sem chave de modelo. Tudo o que ele mostra sem o agente vem de arquivos JSON gerados a partir da gold por `cnpj_pipeline.site_export`. Os arquivos são pequenos, deterministas e versionados no repositório.

```
python -m cnpj_pipeline.site_export --month 2026-10 --gold-dir data/gold --out site/public/data
```

A saída fica em `site/public/data/<mês>/`, com um `index.json` por mês (nome, tamanho e SHA-256 de cada arquivo) e um `months.json` na raiz listando os meses disponíveis.

## O que é seguro publicar, e por quê

- **Só agregados.** O exportador lê apenas os cinco marts e a dimensão de seções da CNAE. Nunca lê `fato_estabelecimento` (uma linha por CNPJ). Um teste esconde o arquivo da fato e confirma que a exportação continua funcionando.
- **Lista de chaves permitidas.** Cada documento é checado contra uma lista fechada de nomes de campo (`ALLOWED_KEYS`). Uma chave nova faz a exportação falhar, e um teste falha se a lista tiver chave que nenhum arquivo usa. Nenhum campo de nome, documento, e-mail, telefone ou endereço existe na lista.
- **Recusa se a gold não reconcilia.** Sem manifesto, com manifesto de outro mês, sem reconciliações registradas ou com qualquer reconciliação bloqueante falhando, nada é gravado.
- **Recortes pequenos não são publicados.** Um recorte com contagem abaixo de 5 (`MIN_CELL`) é descartado, e cada arquivo informa em `celulas_suprimidas` quantos foram omitidos.
- **Município nunca é cruzado com atividade.** Município só aparece no ranking dos 50 maiores. O cruzamento município por divisão da CNAE (mais de 120 mil recortes com menos de 5 unidades no mês 2026-10) não é exportado.
- **Sem data nem hora.** Rodar duas vezes gera arquivos idênticos byte a byte, então o diff no Git mostra só mudança real de dado. Nada vem da máquina que gerou.
- **Tamanho limitado.** Nenhum arquivo passa de 150 KB.

### O que a supressão não faz

O cadastro de CNPJ já é público estabelecimento por estabelecimento, então a supressão não é controle de divulgação. Ela evita destacar contagens minúsculas e instáveis e mantém a regra de publicação igual à do agente. Como os totais nacionais continuam completos, um valor omitido pode ser estimado por diferença, e os arquivos não fingem o contrário.

## Arquivos

| Arquivo | Conteúdo |
| --- | --- |
| `overview.json` | Totais nacionais: estabelecimentos e empresas ativas, Simples e MEI (com e sem organizações políticas), aberturas, sócios, concentração em UFs |
| `uf.json` | Por UF: estabelecimentos e empresas ativas, optantes e percentuais de Simples e MEI |
| `aberturas_ano.json` | Aberturas por ano desde 2000, separando o CNAE de organizações políticas |
| `aberturas_mes.json` | Aberturas por mês desde 2015, com o mesmo corte |
| `aberturas_secao_ano.json` | Aberturas por seção da CNAE e ano, sem organizações políticas |
| `aberturas_uf_ano.json` | Aberturas por UF e ano, sem organizações políticas |
| `ativos_secao.json` | Estabelecimentos ativos, Simples e MEI por seção |
| `municipios_top.json` | Os 50 municípios com mais estabelecimentos ativos |
| `perfil_porte.json` | Empresas ativas por porte, com participação |
| `perfil_natureza.json` | As 12 naturezas jurídicas mais comuns, o resto agrupado, e os totais por grupo |
| `capital_mediano.json` | Capital social mediano por porte e natureza (5 naturezas mais comuns), sem organizações políticas |
| `socios.json` | Vínculos de sócio nas 15 qualificações mais comuns, por tipo de sócio |
| `pipeline.json` | Linhas, bytes e tempos das camadas e as reconciliações da gold, a partir dos manifestos |

Todo arquivo tem o mesmo envelope: `arquivo`, `esquema`, `mes_referencia`, `titulo`, `descricao`, `ressalvas` (o texto de cada ressalva que o site deve mostrar ao lado dos números), `celulas_suprimidas` quando se aplica e `dados`.

## Ressalvas que viajam com os números

- A foto é de um único mês.
- O CNAE 9492800 (organizações políticas) concentra picos de aberturas em anos de eleição municipal. A causa é provável, não demonstrada, e as séries separam o grupo.
- O último ano e o último mês estão incompletos. As linhas correspondentes têm `parcial: true`.
- O capital social é declarado e tem valores extremos, por isso o site usa medianas e nunca somas.
- A série de aberturas só conta estabelecimentos que ainda constam no cadastro, e datas muito antigas podem ser erro de preenchimento.

## Limitações conhecidas

- Medianas de capital não se somam. Por isso `capital_mediano.json` guarda a mediana por célula (porte e natureza) e não oferece totais.
- Uma linha com `uf: null` pode existir quando a UF do cadastro era inválida e foi anulada na silver. O site deve rotulá-la como "sem UF informada".
- Os pontos de partida das séries (2000 para a anual, 2015 para a mensal e os cortes) são escolhas de apresentação, não de qualidade de dado. As aberturas anteriores a 2000 entram apenas como total em `overview.json`.
