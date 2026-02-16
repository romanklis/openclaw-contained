#!/bin/bash
set -e

# First, grant CREATE DATABASE permission to temporal user so it can set up its own schema
psql -v ON_ERROR_STOP=1 --username "postgres" --dbname "postgres" <<-EOSQL
    -- Grant CREATE DATABASE to temporal user
    ALTER USER temporal CREATEDB;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Grant schema permissions to openclaw user
    GRANT ALL ON SCHEMA public TO openclaw;
    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO openclaw;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO openclaw;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO openclaw;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO openclaw;

    -- Switch to temporal database and grant permissions
    \c temporal;
    GRANT ALL ON SCHEMA public TO temporal;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO temporal;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO temporal;

    -- Create initial tables for openclaw
    \c openclaw;
    
    CREATE TABLE IF NOT EXISTS tasks (
        id VARCHAR PRIMARY KEY,
        name VARCHAR NOT NULL,
        description TEXT,
        status VARCHAR NOT NULL DEFAULT 'created',
        workspace_id VARCHAR NOT NULL,
        current_image VARCHAR,
        current_policy_id INTEGER,
        workflow_id VARCHAR UNIQUE,
        workflow_run_id VARCHAR,
        created_by VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP,
        started_at TIMESTAMP,
        completed_at TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS policies (
        id SERIAL PRIMARY KEY,
        task_id VARCHAR NOT NULL REFERENCES tasks(id),
        version INTEGER NOT NULL,
        tools_allowed JSONB DEFAULT '[]',
        network_rules JSONB DEFAULT '{}',
        filesystem_rules JSONB DEFAULT '{}',
        database_rules JSONB DEFAULT '{}',
        resource_limits JSONB DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_by VARCHAR
    );

    CREATE TABLE IF NOT EXISTS capability_requests (
        id SERIAL PRIMARY KEY,
        task_id VARCHAR NOT NULL REFERENCES tasks(id),
        capability_type VARCHAR NOT NULL,
        resource_name VARCHAR NOT NULL,
        justification TEXT NOT NULL,
        details JSONB,
        status VARCHAR NOT NULL DEFAULT 'pending',
        decision_notes TEXT,
        decided_by VARCHAR,
        decided_at TIMESTAMP,
        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id SERIAL PRIMARY KEY,
        task_id VARCHAR REFERENCES tasks(id),
        user_id VARCHAR,
        action VARCHAR NOT NULL,
        resource_type VARCHAR,
        resource_id VARCHAR,
        details JSONB,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Create indexes
    CREATE INDEX idx_tasks_status ON tasks(status);
    CREATE INDEX idx_tasks_created_at ON tasks(created_at);
    CREATE INDEX idx_capability_requests_task_id ON capability_requests(task_id);
    CREATE INDEX idx_capability_requests_status ON capability_requests(status);
    CREATE INDEX idx_audit_logs_task_id ON audit_logs(task_id);
    CREATE INDEX idx_audit_logs_timestamp ON audit_logs(timestamp);
EOSQL
