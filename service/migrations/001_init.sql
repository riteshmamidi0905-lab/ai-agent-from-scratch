-- 001_init: durable runs, ordered events, approvals, evaluation results
CREATE TABLE runs (
    id          TEXT PRIMARY KEY,
    objective   TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('queued','running','awaiting_approval','completed','failed','stopped','escalated')),
    stop_reason TEXT NOT NULL DEFAULT '',
    final_answer TEXT,
    state_json  JSONB,
    evaluation  JSONB,
    tokens      INTEGER NOT NULL DEFAULT 0,
    max_steps   INTEGER NOT NULL,
    request_id  TEXT NOT NULL DEFAULT '',
    event_seq   INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX runs_created_idx ON runs (created_at DESC);
CREATE INDEX runs_status_idx ON runs (status);

CREATE TABLE run_events (
    run_id  TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq     INTEGER NOT NULL,
    type    TEXT NOT NULL,
    payload JSONB NOT NULL,
    ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE approvals (
    id         TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    tool       TEXT NOT NULL,
    args       JSONB NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','denied')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ
);
CREATE INDEX approvals_run_idx ON approvals (run_id);

CREATE TABLE eval_runs (
    id         TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    aggregate  JSONB NOT NULL,
    rows       JSONB NOT NULL
);
