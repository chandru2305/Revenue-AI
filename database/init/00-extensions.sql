-- Runs once when the postgres container initializes an empty data directory.
-- pgcrypto is enabled for future DB-side UUID/hash helpers; application code
-- currently generates UUIDs client-side (see backend/app/db/base.py).
CREATE EXTENSION IF NOT EXISTS pgcrypto;
