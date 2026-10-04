FROM python:3.13-slim@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY watchlist.json ./watchlist.json

RUN pip install --no-cache-dir .

EXPOSE 3001

CMD ["aipa-api", "serve", "--host", "0.0.0.0", "--port", "3001"]
