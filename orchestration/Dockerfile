# Imagem oficial do Airflow + as dependencias do pipeline. O codigo do projeto nao e copiado para a
# imagem: o compose monta ./src em /opt/cnpj/src, entao uma mudanca no codigo vale sem rebuild.
ARG AIRFLOW_VERSION=3.4.0
FROM apache/airflow:${AIRFLOW_VERSION}

# duckdb e requests sao as dependencias do pipeline; boto3 so e usado na publicacao para a AWS (o
# Airflow ja traz boto3 com o provedor da Amazon, mas o minimo exigido pelo projeto fica explicito);
# pytest permite rodar a checagem de integridade da DAG dentro do conteiner.
RUN pip install --no-cache-dir "duckdb>=1.1" "requests>=2.31" "boto3>=1.34" "pytest>=8.0"

ENV PYTHONPATH=/opt/cnpj/src \
    CNPJ_DATA_DIR=/opt/cnpj/data
