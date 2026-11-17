-- The index tables of the Java FAIR Data Point (FAIRDataTeam/FAIRDataPoint,
-- src/main/resources/db/migration/V1__baseline.sql, MIT licence), used as the
-- source schema in the importer's integration test.

CREATE TABLE index_settings
(
    uuid                      UUID                     NOT NULL,
    auto_permit               BOOLEAN,
    retrieval_rate_limit_wait TEXT,
    retrieval_timeout         TEXT,
    ping_valid_duration       TEXT,
    ping_rate_limit_duration  TEXT,
    ping_rate_limit_hits      INTEGER,
    created_at                TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at                TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk__index_settings PRIMARY KEY (uuid)
);

CREATE TABLE index_settings_ping_deny
(
    index_settings_id UUID NOT NULL,
    pattern           TEXT NOT NULL,
    CONSTRAINT pk__index_settings_ping_deny PRIMARY KEY (index_settings_id, pattern),
    CONSTRAINT fk__index_settings_ping_deny_settings FOREIGN KEY (index_settings_id) REFERENCES index_settings (uuid) ON DELETE CASCADE
);

CREATE TABLE index_entry
(
    uuid              UUID                     NOT NULL,
    client_url        TEXT                     NOT NULL,
    state             VARCHAR(16)              NOT NULL,
    permit            VARCHAR(16)              NOT NULL,
    metadata_version  INTEGER                  NOT NULL,
    repository_uri    TEXT,
    metadata          TEXT                     NOT NULL, -- JSON object of harvested metadata
    last_retrieval_at TIMESTAMP WITH TIME ZONE,
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at        TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk__index_entry PRIMARY KEY (uuid),
    CONSTRAINT uq__index_entry_client_url UNIQUE (client_url),
    CONSTRAINT ck__index_entry_state CHECK (state IN ('Unknown', 'Valid', 'Invalid', 'Unreachable')),
    CONSTRAINT ck__index_entry_permit CHECK (permit IN ('PENDING', 'ACCEPTED', 'REJECTED'))
);

CREATE TABLE index_event
(
    uuid         UUID                     NOT NULL,
    type         VARCHAR(32)              NOT NULL,
    version      INTEGER                  NOT NULL,
    payload      TEXT                     NOT NULL, -- JSON, shape depends on type
    remote_addr  TEXT,
    triggered_by UUID,
    related_to   UUID,
    executed_at  TIMESTAMP WITH TIME ZONE,
    finished_at  TIMESTAMP WITH TIME ZONE,
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk__index_event PRIMARY KEY (uuid),
    CONSTRAINT ck__index_event_type CHECK (type IN ('AdminTrigger', 'MetadataRetrieval', 'WebhookTrigger', 'IncomingPing', 'WebhookPing')),
    CONSTRAINT fk__index_event_triggered_by FOREIGN KEY (triggered_by) REFERENCES index_event (uuid),
    CONSTRAINT fk__index_event_related_to FOREIGN KEY (related_to) REFERENCES index_entry (uuid) ON DELETE CASCADE
);

CREATE TABLE index_webhook
(
    uuid        UUID                     NOT NULL,
    payload_url TEXT                     NOT NULL,
    secret      TEXT                     NOT NULL,
    all_events  BOOLEAN                  NOT NULL,
    all_entries BOOLEAN                  NOT NULL,
    enabled     BOOLEAN                  NOT NULL,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk__index_webhook PRIMARY KEY (uuid)
);

CREATE TABLE index_webhook_event
(
    webhook_id UUID        NOT NULL,
    event      VARCHAR(32) NOT NULL,
    CONSTRAINT pk__index_webhook_event PRIMARY KEY (webhook_id, event),
    CONSTRAINT ck__index_webhook_event CHECK (event IN ('NewEntry', 'IncomingPing', 'EntryValid', 'EntryInvalid', 'EntryUnreachable', 'AdminTrigger', 'WebhookPing')),
    CONSTRAINT fk__index_webhook_event_webhook FOREIGN KEY (webhook_id) REFERENCES index_webhook (uuid) ON DELETE CASCADE
);

CREATE TABLE index_webhook_entry
(
    webhook_id UUID NOT NULL,
    entry      TEXT NOT NULL,
    CONSTRAINT pk__index_webhook_entry PRIMARY KEY (webhook_id, entry),
    CONSTRAINT fk__index_webhook_entry_webhook FOREIGN KEY (webhook_id) REFERENCES index_webhook (uuid) ON DELETE CASCADE
);
