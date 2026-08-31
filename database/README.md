# database/

PostgreSQL is the source-of-truth database (see `docker-compose.yml`).

- **Schema migrations** live in `backend/alembic/`, not here — they're
  colocated with the SQLAlchemy models they're generated from
  (`backend/app/models/`). Run them with
  `cd backend && python -m alembic upgrade head`.
- **`init/`** holds SQL that the Postgres container runs once, automatically,
  the first time it initializes an empty data directory (mounted at
  `/docker-entrypoint-initdb.d` in `docker-compose.yml`). Currently just
  enables the `pgcrypto` extension for future DB-side UUID/hash helpers —
  application code generates UUIDs client-side today
  (`backend/app/db/base.py::new_uuid`).

This directory is for infrastructure-level database setup, not
application-level schema — that distinction is why it's separate from
`backend/`.
