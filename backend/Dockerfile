# RecoverAI backend — FastAPI + SQLAlchemy.
#
# `runtime` is the production image: prod deps only, non-root, runs
# `alembic upgrade head` then uvicorn. `dev` extends it with the test
# dependencies and the test suite for `make test`.

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Migrations are idempotent, so running them on every start is safe and
# keeps a fresh database in sync automatically.
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]


FROM runtime AS dev

USER root
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY pyproject.toml ./
COPY tests ./tests
USER appuser

CMD ["python", "-m", "pytest", "-q"]
