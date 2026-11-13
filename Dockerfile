FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instala dependencias antes do codigo para aproveitar o cache de camadas
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e ".[dev]"

COPY tests ./tests

CMD ["python", "-m", "cnpj_pipeline.download", "--help"]
