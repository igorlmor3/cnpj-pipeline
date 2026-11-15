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
# A Receita Federal publica os arquivos em um compartilhamento publico Nextcloud, acessado por
# WebDAV. O padrao abaixo (pasta Dados/Cadastros/CNPJ/<AAAA-MM>/<arquivo>.zip) e o usado nos
# downloads do mes 2026-10. Para outro caminho ou espelho, sobrescreva com CNPJ_URL_TEMPLATE
# no .env ou com --url-template na linha de comando.
OFFICIAL_BASE_URL = "https://arquivos.receitafederal.gov.br/public.php/webdav/Dados/Cadastros/CNPJ"
DEFAULT_URL_TEMPLATE = OFFICIAL_BASE_URL + "/{month}/{filename}"
WEBDAV_MARKER = "/public.php/webdav/"

# Token do compartilhamento publico (e o "usuario" do login WebDAV, com senha vazia). Nao e um
# segredo: faz parte do link publico. Ele pode mudar; sobrescreva com CNPJ_SHARE_TOKEN.
DEFAULT_SHARE_TOKEN = "gn672Ad4CF8N6TK"
SHARE_TOKEN = os.getenv("CNPJ_SHARE_TOKEN", DEFAULT_SHARE_TOKEN)
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
