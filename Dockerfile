# Imagem do backend NetSentinel — roda a API FastAPI que também serve o
# frontend estático (HTML/JS/CSS) via StaticFiles. Um único container cobre
# API + UI; o PostgreSQL fica em outro serviço (ver docker-compose.yml).

FROM python:3.12-slim

# iputils-ping → o monitor de devices HARDWARE faz a checagem via `ping`
# (subprocess). Sem o binário, todo device HARDWARE apareceria como DOWN.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências primeiro: camada cacheável — só reinstala se requirements mudar.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação. O frontend é servido pelo backend a partir de
# Path(__file__).parent.parent / "frontend", logo ambos vivem sob /app.
COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8000

# APENAS 1 worker: o loop de monitoramento roda in-process (evento de startup
# do FastAPI). Múltiplos workers duplicariam checagens, alertas e gravações.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
