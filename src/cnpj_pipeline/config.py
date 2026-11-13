"""Configuracao central do projeto.

Os valores podem ser sobrescritos por variaveis de ambiente (ver .env.example)
ou por argumentos de linha de comando nos scripts.
"""

from __future__ import annotations

import os
from pathlib import Path

# Raiz do repositorio (src/cnpj_pipeline/config.py -> raiz)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = Path(os.getenv("CNPJ_DATA_DIR", PROJECT_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"

# Template da URL de cada arquivo. Placeholders: {month} (AAAA-MM) e {filename}.
#
# ATENCAO: a Receita Federal alterou o layout da pagina e o caminho dos
# arquivos no fim de janeiro de 2026. O valor padrao abaixo segue o padrao
# historico do repositorio e PRECISA ser confirmado no site oficial antes do
# primeiro uso. Se o caminho mudou, sobrescreva com CNPJ_URL_TEMPLATE no .env
# ou com --url-template na linha de comando.
DEFAULT_URL_TEMPLATE = (
    "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/{month}/{filename}"
)
URL_TEMPLATE = os.getenv("CNPJ_URL_TEMPLATE", DEFAULT_URL_TEMPLATE)

# Tabelas do conjunto de dados e como os arquivos sao nomeados.
# Tabelas "divididas" sao publicadas em varias partes: Empresas0.zip ... Empresas9.zip.
# Tabelas "unicas" sao publicadas em um unico arquivo: Cnaes.zip.
SPLIT_TABLES = ("Empresas", "Estabelecimentos", "Socios")
SINGLE_TABLES = (
    "Cnaes",
    "Motivos",
    "Municipios",
    "Naturezas",
    "Paises",
    "Qualificacoes",
    "Simples",
)
ALL_TABLES = SPLIT_TABLES + SINGLE_TABLES

# Numero de partes das tabelas divididas (0..N-1). Ajuste conforme o mes baixado.
DEFAULT_PARTS = int(os.getenv("CNPJ_PARTS", "10"))
