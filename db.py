import os

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/imfasterthanyou")

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS runner_fallback_seq
    START WITH -1 INCREMENT BY -1;

CREATE TABLE IF NOT EXISTS meets (
    meet_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    date TEXT,
    sport TEXT NOT NULL CHECK (sport IN ('xc', 'track')),
    state TEXT,
    url TEXT NOT NULL,
    scraped BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS runners (
    runner_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL
);

-- Runners without a stable TFRRS athlete id fall back to a synthetic id
-- derived from name+team; this table lets repeated sightings of that
-- fallback resolve to the same runner_id.
CREATE TABLE IF NOT EXISTS runner_fallback_keys (
    fallback_key TEXT PRIMARY KEY,
    runner_id BIGINT NOT NULL REFERENCES runners(runner_id)
);

CREATE TABLE IF NOT EXISTS results (
    result_id SERIAL PRIMARY KEY,
    meet_id INTEGER NOT NULL REFERENCES meets(meet_id),
    event TEXT NOT NULL,
    gender TEXT NOT NULL,
    place INTEGER NOT NULL,
    runner_id BIGINT NOT NULL REFERENCES runners(runner_id),
    team TEXT,
    time TEXT
);

CREATE INDEX IF NOT EXISTS idx_results_race ON results (meet_id, event, gender);
CREATE INDEX IF NOT EXISTS idx_results_runner ON results (runner_id);
CREATE INDEX IF NOT EXISTS idx_runners_name ON runners (name);
"""


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_schema(conn=None):
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
