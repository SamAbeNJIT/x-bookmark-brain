# Container image for the web app (App Runner / any container host).
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching). psycopg[binary] bundles libpq, so no
# system packages are needed.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# App Runner sets PORT; default to 8080 locally. Health check path: /health.
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn xbb.web:app --host 0.0.0.0 --port ${PORT:-8080}"]
