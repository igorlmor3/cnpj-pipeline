"""Perguntas sobre o projeto, com a evidencia que um trecho precisa conter para responder a cada uma.

Um trecho e relevante para uma pergunta se contem TODAS as cadeias de `evidence` (sem diferenca de
caixa nem de acento). Medir assim, em vez de por nome de arquivo, evita contar como erro o caso de a
mesma informacao estar em dois documentos, e deixa o conjunto valido quando um trecho e movido.

Dois conjuntos, com a mesma regra do conjunto reservado da avaliacao do agente:
- `dev`: serve para ajustar a busca (tokenizacao, pesos, tamanho dos trechos);
- `reservado`: escrito antes de qualquer medicao e nao usado para ajustar nada. Se a busca for
  ajustada olhando para ele, os casos passam a ser de desenvolvimento e sao substituidos.
As perguntas usam o vocabulario de quem pergunta, nao o dos documentos.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalCase:
    id: str
    category: str  # decisao, regra, numero, limite, privacidade, ressalva, definicao, infra
    question: str
    evidence: tuple[str, ...]


DEV_CASES: tuple[RetrievalCase, ...] = (
    RetrievalCase(
        "d_duckdb",
        "decisao",
        "Por que o projeto usa DuckDB em vez de Spark ou Polars?",
        ("Lê CSV maior que a memória",),
    ),
    RetrievalCase(
        "d_duplicado_silver",
        "regra",
        "Como a silver escolhe qual linha manter quando o CNPJ básico está duplicado?",
        ("a mais preenchida", "hash"),
    ),
    RetrievalCase(
        "d_cnpj_zero",
        "definicao",
        "O que significa o cnpj_basico 00000000?",
        ("Banco do Brasil",),
    ),
    RetrievalCase(
        "d_resposta_sem_consulta",
        "limite",
        "O que o visitante vê quando o modelo responde sem consultar nada?",
        ("recusa fixa",),
    ),
    RetrievalCase(
        "d_gold_sem_contato",
        "privacidade",
        "A gold tem nomes, endereços ou telefones?",
        ("Sem dados de contato, endereço nem nomes",),
    ),
    RetrievalCase(
        "d_celulas_pequenas",
        "privacidade",
        "Por que o site não publica recortes com poucas ocorrências?",
        ("MIN_CELL",),
    ),
    RetrievalCase(
        "d_particoes_athena",
        "decisao",
        "Como o Athena descobre os meses novos sem rodar crawler?",
        ("Partition projection", "sem crawler"),
    ),
    RetrievalCase(
        "d_digito_verificador",
        "numero",
        "Quantos CNPJs tinham dígito verificador inválido no mês real?",
        ("0 de 73.809.379",),
    ),
    RetrievalCase(
        "d_exclusao_simples_zerada",
        "definicao",
        "O que significam as datas de exclusão do Simples zeradas?",
        ("00000000", "não foi excluída"),
    ),
    RetrievalCase(
        "d_picos_eleicao",
        "ressalva",
        "Por que há picos de aberturas em anos de eleição?",
        ("9492800", "picos de aberturas"),
    ),
    RetrievalCase(
        "d_tempo_silver",
        "numero",
        "Quanto tempo levou a camada silver de Estabelecimentos?",
        ("1128,9",),
    ),
    RetrievalCase(
        "d_github_pages",
        "decisao",
        "Por que o site fica no GitHub Pages e não na AWS?",
        ("encerra a conta em 6 meses",),
    ),
    RetrievalCase(
        "d_guarda_recusa",
        "limite",
        "O que a guarda de SQL recusa?",
        ("Mais de uma instrução", "DDL"),
    ),
    RetrievalCase(
        "d_teto_gasto",
        "limite",
        "Qual o teto de gasto mensal do agente com o modelo?",
        ("teto de US$ 5 por mês",),
    ),
    RetrievalCase(
        "d_idempotencia",
        "regra",
        "Como a idempotência das etapas é garantida?",
        ("arquivo temporário", "renomeado"),
    ),
    RetrievalCase(
        "d_bronze_rejeitadas",
        "regra",
        "O que acontece com linhas malformadas na camada bronze?",
        ("_rejects",),
    ),
    RetrievalCase(
        "d_agente_sem_fato",
        "privacidade",
        "Por que o agente não lê o fato por estabelecimento?",
        ("uma linha por CNPJ", "não está na lista"),
    ),
    RetrievalCase(
        "d_encoding",
        "regra",
        "Qual o encoding dos arquivos da Receita e como é tratado?",
        ("ISO-8859-1", "UTF-8"),
    ),
    RetrievalCase(
        "d_somar_meses",
        "limite",
        "Por que o agente recusa somar empresas ativas de meses diferentes?",
        ("contaria o mesmo CNPJ",),
    ),
    RetrievalCase(
        "d_recursos_terraform",
        "infra",
        "Que recursos o Terraform cria na AWS?",
        ("Data lake privado",),
    ),
    RetrievalCase(
        "d_dag_sem_agenda",
        "infra",
        "Por que a DAG do Airflow não tem agenda?",
        ("schedule=None",),
    ),
    RetrievalCase(
        "d_mart_movimentos",
        "definicao",
        "O que é o mart de movimentos entre meses?",
        ("mart_movimentos", "fotos consecutivas"),
    ),
    RetrievalCase(
        "d_verificacoes_passaram",
        "numero",
        "Quantas verificações de qualidade passaram na execução completa?",
        ("73 verificações", "66 ok"),
    ),
    RetrievalCase(
        "d_estabelecimento_nao_e_empresa",
        "ressalva",
        "Estabelecimento é a mesma coisa que empresa nos marts?",
        ("Estabelecimento não é empresa",),
    ),
)

RESERVADO_CASES: tuple[RetrievalCase, ...] = (
    RetrievalCase(
        "r_sql_puro_vs_dbt",
        "decisao",
        "Por que a gold é construída em SQL puro e não com dbt?",
        ("SQL puro do DuckDB",),
    ),
    RetrievalCase(
        "r_camadas",
        "definicao",
        "Quais são as camadas do pipeline e o que cada uma guarda?",
        ("Imutável. Nunca editar.",),
    ),
    RetrievalCase(
        "r_telefone_zeros",
        "regra",
        "O que a silver faz com telefones preenchidos só com zeros?",
        ("só de zeros vira NULL",),
    ),
    RetrievalCase(
        "r_motivo_zero",
        "definicao",
        "O que quer dizer motivo de situação cadastral 00?",
        ("sem motivo",),
    ),
    RetrievalCase(
        "r_cpf_mascarado",
        "privacidade",
        "Os CPFs dos sócios aparecem completos nos dados?",
        ("mascarados pela própria Receita",),
    ),
    RetrievalCase(
        "r_linhas_fato",
        "numero",
        "Quantas linhas tem a tabela fato da gold?",
        ("73.809.379", "fato_estabelecimento"),
    ),
    RetrievalCase(
        "r_municipio_atividade",
        "privacidade",
        "O site pode mostrar o cruzamento de município com atividade econômica?",
        ("Município nunca é cruzado com atividade",),
    ),
    RetrievalCase(
        "r_capital_mediana",
        "ressalva",
        "Por que o capital social é resumido com mediana e não com soma?",
        ("O capital social é declarado", "medianas"),
    ),
    RetrievalCase(
        "r_consultas_caras",
        "limite",
        "Como o agente evita consultas caras?",
        ("máximo de 4 junções",),
    ),
    RetrievalCase(
        "r_tempo_consulta",
        "limite",
        "Quanto tempo uma consulta pode levar antes de ser cancelada?",
        ("30 s por consulta",),
    ),
    RetrievalCase(
        "r_comparacao_meses",
        "decisao",
        "Como é feita a comparação entre dois meses do cadastro?",
        ("junção completa por CNPJ",),
    ),
    RetrievalCase(
        "r_chaves_iam",
        "infra",
        "Por que as chaves de acesso do usuário de publicação não são criadas pelo Terraform?",
        ("nunca ficam gravadas no arquivo de estado",),
    ),
    RetrievalCase(
        "r_portao_qualidade",
        "infra",
        "O que acontece com a publicação na nuvem se a verificação de qualidade falhar?",
        ("nada vai para o site nem para a nuvem com dado reprovado",),
    ),
    RetrievalCase(
        "r_limite_athena",
        "infra",
        "Qual o limite de dados lidos por consulta no workgroup do Athena?",
        ("padrão 5 GB",),
    ),
    RetrievalCase(
        "r_bytes_lidos_fato",
        "infra",
        "Quantos megabytes uma consulta sobre o fato inteiro leu no Athena?",
        ("39,52 MB",),
    ),
    RetrievalCase(
        "r_cnae_9492800",
        "definicao",
        "O que é o CNAE 9492800?",
        ("9492800", "organizações políticas"),
    ),
    RetrievalCase(
        "r_texto_para_sql_vs_rag",
        "decisao",
        "Por que o agente usa texto para SQL em vez de RAG com embeddings?",
        ("contagens exatas", "embeddings não fornecem"),
    ),
    RetrievalCase(
        "r_arquivos_deterministas",
        "regra",
        "Os arquivos do site mudam a cada execução do exportador?",
        ("byte a byte",),
    ),
    RetrievalCase(
        "r_tamanho_json",
        "limite",
        "Qual o tamanho máximo de cada arquivo JSON do site?",
        ("Nenhum arquivo passa de 150 KB",),
    ),
    RetrievalCase(
        "r_aberturas_fechadas",
        "ressalva",
        "A série de aberturas inclui os estabelecimentos que já fecharam?",
        ("só conta estabelecimentos que ainda constam no cadastro",),
    ),
    RetrievalCase(
        "r_limite_provedor",
        "limite",
        "Onde fica o limite de gasto do provedor do modelo de linguagem?",
        ("limite de gasto configurado na própria conta do provedor",),
    ),
    RetrievalCase(
        "r_mes_novo_api",
        "infra",
        "O que fazer para a API passar a consultar um mês novo?",
        ("acrescente-o à lista", "terraform apply"),
    ),
    RetrievalCase(
        "r_decisoes_em_aberto",
        "decisao",
        "Que decisões de arquitetura ainda estão em aberto?",
        ("Particionamento do Parquet", "Em aberto"),
    ),
    RetrievalCase(
        "r_tempo_airflow",
        "infra",
        "Quanto tempo leva rodar o mês real pelo Airflow?",
        ("O mês real leva horas",),
    ),
)

SUITES: dict[str, tuple[RetrievalCase, ...]] = {
    "dev": DEV_CASES,
    "reservado": RESERVADO_CASES,
    "all": DEV_CASES + RESERVADO_CASES,
}
