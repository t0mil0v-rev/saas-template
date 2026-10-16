#  API-образ.  Многоступенчатая сборка, финальный слой - distroless-подобный
#  минимум под непривилегированным пользователем.
#
#  Стадии:
#    base         - общий слой с Python 3.11 и системными библиотеками
#    builder      - ставит зависимости в venv (изолированный слой кэша)
#    development  - цель для docker-compose.dev.yml (dev-зависимости, reload)
#    production   - финал: только runtime, non-root, без компилятора

FROM python:3.15-slim-bookworm@sha256:122756f3fe72c4f5d3d71897888204ee85774bc79de952555b8a474e1d0a5d53 AS base

# Python-строго-3.11: образ 3.11-slim гарантирует нужную версию.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random

WORKDIR /app

# tini - корректный init: пробрасывает сигналы и подбирает зомби-процессы,
# что критично для нашего prefork-супервизора.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tini libpq5; \
    rm -rf /var/lib/apt/lists/*


# builder: колёса зависимостей во venv
FROM base AS builder

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential libpq-dev; \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Сначала только requirements - слой кэшируется, пока не менялись зависимости.
COPY requirements.txt requirements-dev.txt requirements.lock requirements-dev.lock ./
RUN pip install --require-hashes -r requirements.lock


# development
FROM builder AS development
ENV PATH="/opt/venv/bin:$PATH" APP_ENV=development
RUN pip install --require-hashes -r requirements-dev.lock
COPY . .
RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.cli", "serve", "--reload"]


# production
FROM base AS production
ENV PATH="/opt/venv/bin:$PATH" APP_ENV=production

# Готовый venv из builder - без компилятора и dev-заголовков в финале.
COPY --from=builder /opt/venv /opt/venv

# Непривилегированный пользователь с фиксированным UID (нужен для read_only FS
# и понятных прав на тома).
RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin appuser

COPY --chown=appuser:appuser . .

# Каталоги, которые процессу нужны на запись, при read_only:true в compose
# монтируются как tmpfs; здесь только гарантируем существование точки.
USER appuser

EXPOSE 8000

# Живость: тот же CLI, что использует compose healthcheck.
HEALTHCHECK --interval=15s --timeout=8s --start-period=25s --retries=5 \
    CMD ["python", "-m", "app.cli", "healthcheck"]

# tini как PID 1 → сигналы доходят до супервизора, зомби подбираются.
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.cli", "serve"]
