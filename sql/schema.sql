-- =============================================================================
-- NEXUS BI — Relational schema (PostgreSQL 16+)
-- -----------------------------------------------------------------------------
-- Source dataset : Olist Brazilian E-Commerce Public Dataset (real, anonymised)
--                  https://github.com/olist/work-at-olist-data
-- Schemas        : core -> normalised business entities (3NF)
--                  ops  -> pipeline runs and data-quality audit trail
--                  ml   -> model runs, evaluations and predictions
--                  (analytics views are created later in sql/views.sql)
--
-- Key conventions
--   *_key   surrogate integer primary keys (compact joins and indexes)
--   *_uid   natural 32-char hex identifiers kept from the source system
--   Source timestamps are local Brazil time (America/Sao_Paulo) and are stored
--   as TIMESTAMP WITHOUT TIME ZONE, exactly as provided by Olist.
--   Audit columns (created_at / updated_at) are TIMESTAMPTZ.
--
-- The script is idempotent: it can be re-run safely and never drops data.
-- =============================================================================

SET client_min_messages = warning;   -- hide "already exists, skipping" notices on re-runs

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ops;

-- Keeps updated_at current on every UPDATE.
CREATE OR REPLACE FUNCTION core.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


-- =============================================================================
-- GEOGRAPHY
-- =============================================================================

-- Brazilian federative units (IBGE reference data: 26 states + Federal District).
CREATE TABLE IF NOT EXISTS core.states (
    state_code   CHAR(2)      PRIMARY KEY,
    state_name   VARCHAR(40)  NOT NULL UNIQUE,
    region       VARCHAR(20)  NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_states_code   CHECK (state_code ~ '^[A-Z]{2}$'),
    CONSTRAINT ck_states_region CHECK (region IN ('Norte', 'Nordeste', 'Centro-Oeste', 'Sudeste', 'Sul'))
);

INSERT INTO core.states (state_code, state_name, region) VALUES
    ('AC', 'Acre',                'Norte'),
    ('AL', 'Alagoas',             'Nordeste'),
    ('AP', 'Amapá',               'Norte'),
    ('AM', 'Amazonas',            'Norte'),
    ('BA', 'Bahia',               'Nordeste'),
    ('CE', 'Ceará',               'Nordeste'),
    ('DF', 'Distrito Federal',    'Centro-Oeste'),
    ('ES', 'Espírito Santo',      'Sudeste'),
    ('GO', 'Goiás',               'Centro-Oeste'),
    ('MA', 'Maranhão',            'Nordeste'),
    ('MT', 'Mato Grosso',         'Centro-Oeste'),
    ('MS', 'Mato Grosso do Sul',  'Centro-Oeste'),
    ('MG', 'Minas Gerais',        'Sudeste'),
    ('PA', 'Pará',                'Norte'),
    ('PB', 'Paraíba',             'Nordeste'),
    ('PR', 'Paraná',              'Sul'),
    ('PE', 'Pernambuco',          'Nordeste'),
    ('PI', 'Piauí',               'Nordeste'),
    ('RJ', 'Rio de Janeiro',      'Sudeste'),
    ('RN', 'Rio Grande do Norte', 'Nordeste'),
    ('RS', 'Rio Grande do Sul',   'Sul'),
    ('RO', 'Rondônia',            'Norte'),
    ('RR', 'Roraima',             'Norte'),
    ('SC', 'Santa Catarina',      'Sul'),
    ('SP', 'São Paulo',           'Sudeste'),
    ('SE', 'Sergipe',             'Nordeste'),
    ('TO', 'Tocantins',           'Norte')
ON CONFLICT (state_code) DO NOTHING;

-- One row per (zip-code prefix, city, state). Coordinates are the median of the
-- raw geolocation points for that prefix, after removing points outside Brazil.
-- They are NULL when the source has no valid point for the prefix.
CREATE TABLE IF NOT EXISTS core.locations (
    location_key     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    zip_code_prefix  CHAR(5)       NOT NULL,
    city             VARCHAR(60)   NOT NULL,
    state_code       CHAR(2)       NOT NULL REFERENCES core.states (state_code),
    latitude         NUMERIC(9, 6),
    longitude        NUMERIC(9, 6),
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_locations_zip_city_state UNIQUE (zip_code_prefix, city, state_code),
    CONSTRAINT ck_locations_zip     CHECK (zip_code_prefix ~ '^[0-9]{5}$'),
    CONSTRAINT ck_locations_city    CHECK (city = lower(btrim(city)) AND city <> ''),
    CONSTRAINT ck_locations_coords  CHECK ((latitude IS NULL) = (longitude IS NULL)),
    -- Bounding box of Brazil (including a small margin).
    CONSTRAINT ck_locations_lat     CHECK (latitude  BETWEEN -34.0 AND 5.5),
    CONSTRAINT ck_locations_lng     CHECK (longitude BETWEEN -74.5 AND -34.5)
);

CREATE INDEX IF NOT EXISTS ix_locations_state ON core.locations (state_code);
CREATE INDEX IF NOT EXISTS ix_locations_city  ON core.locations (city, state_code);


-- =============================================================================
-- CALENDAR (date dimension)
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.calendar (
    date_key      DATE         PRIMARY KEY,
    year          SMALLINT     NOT NULL,
    quarter       SMALLINT     NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    month         SMALLINT     NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name    VARCHAR(10)  NOT NULL,
    year_month    CHAR(7)      NOT NULL,                        -- 'YYYY-MM'
    iso_week      SMALLINT     NOT NULL CHECK (iso_week BETWEEN 1 AND 53),
    day_of_month  SMALLINT     NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
    day_of_week   SMALLINT     NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),  -- ISO: 1 = Monday
    day_name      VARCHAR(10)  NOT NULL,
    is_weekend    BOOLEAN      NOT NULL,
    is_holiday    BOOLEAN      NOT NULL DEFAULT FALSE,           -- Brazilian national holidays
    holiday_name  VARCHAR(60),
    CONSTRAINT ck_calendar_holiday CHECK (is_holiday = (holiday_name IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_calendar_year_month ON core.calendar (year_month);


-- =============================================================================
-- PRODUCT CATALOGUE
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.categories (
    category_key          SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_name_pt      VARCHAR(60)    NOT NULL UNIQUE,
    category_name_en      VARCHAR(60)    NOT NULL,
    estimated_cost_ratio  NUMERIC(4, 3)  NOT NULL,
    created_at            TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT ck_categories_cost_ratio CHECK (estimated_cost_ratio > 0 AND estimated_cost_ratio < 1)
);

COMMENT ON COLUMN core.categories.estimated_cost_ratio IS
    'SYNTHETIC. Olist does not publish product costs. Baseline COGS / price ratio '
    'assumed for the category (COST_RATIO_RULES in data_pipeline/transformation.py). Used only to '
    'estimate profit and margin.';

-- Olist anonymises product names: a product is identified by its hash and category.
CREATE TABLE IF NOT EXISTS core.products (
    product_key           INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_uid           CHAR(32)       NOT NULL UNIQUE,
    category_key          SMALLINT       NOT NULL REFERENCES core.categories (category_key),
    name_length           SMALLINT       CHECK (name_length > 0),
    description_length    SMALLINT       CHECK (description_length > 0),
    photos_qty            SMALLINT       CHECK (photos_qty >= 0),
    weight_g              INTEGER        CHECK (weight_g > 0),
    length_cm             SMALLINT       CHECK (length_cm > 0),
    height_cm             SMALLINT       CHECK (height_cm > 0),
    width_cm              SMALLINT       CHECK (width_cm > 0),
    estimated_cost_ratio  NUMERIC(4, 3)  NOT NULL,
    created_at            TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT ck_products_uid        CHECK (product_uid ~ '^[0-9a-f]{32}$'),
    CONSTRAINT ck_products_cost_ratio CHECK (estimated_cost_ratio > 0 AND estimated_cost_ratio < 1)
);

COMMENT ON COLUMN core.products.estimated_cost_ratio IS
    'SYNTHETIC. Category baseline ratio plus a deterministic per-product variation '
    '(seeded by product_uid, so it is reproducible). Not real Olist data.';

CREATE INDEX IF NOT EXISTS ix_products_category ON core.products (category_key);


-- =============================================================================
-- PARTIES
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.sellers (
    seller_key    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_uid    CHAR(32)     NOT NULL UNIQUE,
    location_key  INTEGER      NOT NULL REFERENCES core.locations (location_key),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_sellers_uid CHECK (seller_uid ~ '^[0-9a-f]{32}$')
);

CREATE INDEX IF NOT EXISTS ix_sellers_location ON core.sellers (location_key);

-- A real person. Olist's per-order "customer_id" is NOT a customer: the stable
-- identifier is "customer_unique_id", stored here as customer_uid.
CREATE TABLE IF NOT EXISTS core.customers (
    customer_key  INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_uid  CHAR(32)     NOT NULL UNIQUE,
    location_key  INTEGER      NOT NULL REFERENCES core.locations (location_key),  -- most recent address
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_customers_uid CHECK (customer_uid ~ '^[0-9a-f]{32}$')
);

CREATE INDEX IF NOT EXISTS ix_customers_location ON core.customers (location_key);


-- =============================================================================
-- SALES TRANSACTIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.orders (
    order_key                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_uid                CHAR(32)     NOT NULL UNIQUE,
    customer_key             INTEGER      NOT NULL REFERENCES core.customers (customer_key),
    delivery_location_key    INTEGER      NOT NULL REFERENCES core.locations (location_key),
    order_status             VARCHAR(12)  NOT NULL,
    purchased_at             TIMESTAMP    NOT NULL,
    purchase_date            DATE         GENERATED ALWAYS AS (purchased_at::date) STORED
                                          REFERENCES core.calendar (date_key),
    approved_at              TIMESTAMP,
    delivered_carrier_at     TIMESTAMP,
    delivered_customer_at    TIMESTAMP,
    estimated_delivery_date  DATE         NOT NULL,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_orders_uid    CHECK (order_uid ~ '^[0-9a-f]{32}$'),
    CONSTRAINT ck_orders_status CHECK (order_status IN (
        'created', 'approved', 'invoiced', 'processing',
        'shipped', 'delivered', 'canceled', 'unavailable')),
    -- Chronology rules that must always hold. Violations in the raw data are
    -- repaired or nulled by the pipeline and logged in ops.data_quality_issues.
    CONSTRAINT ck_orders_approved_after_purchase  CHECK (approved_at           >= purchased_at),
    CONSTRAINT ck_orders_carrier_after_purchase   CHECK (delivered_carrier_at  >= purchased_at),
    CONSTRAINT ck_orders_delivered_after_purchase CHECK (delivered_customer_at >= purchased_at),
    CONSTRAINT ck_orders_estimate_after_purchase  CHECK (estimated_delivery_date >= purchased_at::date)
);

CREATE INDEX IF NOT EXISTS ix_orders_customer       ON core.orders (customer_key);
CREATE INDEX IF NOT EXISTS ix_orders_purchase_date  ON core.orders (purchase_date);
CREATE INDEX IF NOT EXISTS ix_orders_status         ON core.orders (order_status);
CREATE INDEX IF NOT EXISTS ix_orders_location       ON core.orders (delivery_location_key);
-- Most analytics read customer history in date order (recency, churn, CLV).
CREATE INDEX IF NOT EXISTS ix_orders_customer_date  ON core.orders (customer_key, purchased_at);

-- Olist stores one row per unit sold: line_number is the item's position in the order.
CREATE TABLE IF NOT EXISTS core.order_items (
    order_key          INTEGER         NOT NULL REFERENCES core.orders (order_key) ON DELETE CASCADE,
    line_number        SMALLINT        NOT NULL,
    product_key        INTEGER         NOT NULL REFERENCES core.products (product_key),
    seller_key         INTEGER         NOT NULL REFERENCES core.sellers (seller_key),
    shipping_limit_at  TIMESTAMP       NOT NULL,
    unit_price         NUMERIC(10, 2)  NOT NULL,
    freight_value      NUMERIC(10, 2)  NOT NULL,
    created_at         TIMESTAMPTZ     NOT NULL DEFAULT now(),
    PRIMARY KEY (order_key, line_number),
    CONSTRAINT ck_items_line    CHECK (line_number >= 1),
    CONSTRAINT ck_items_price   CHECK (unit_price > 0),
    CONSTRAINT ck_items_freight CHECK (freight_value >= 0)
);

CREATE INDEX IF NOT EXISTS ix_items_product ON core.order_items (product_key);
CREATE INDEX IF NOT EXISTS ix_items_seller  ON core.order_items (seller_key);

CREATE TABLE IF NOT EXISTS core.order_payments (
    order_key           INTEGER         NOT NULL REFERENCES core.orders (order_key) ON DELETE CASCADE,
    payment_sequential  SMALLINT        NOT NULL,
    payment_type        VARCHAR(12)     NOT NULL,
    installments        SMALLINT        NOT NULL,
    amount              NUMERIC(12, 2)  NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    PRIMARY KEY (order_key, payment_sequential),
    CONSTRAINT ck_payments_sequential   CHECK (payment_sequential >= 1),
    CONSTRAINT ck_payments_type         CHECK (payment_type IN (
        'credit_card', 'boleto', 'voucher', 'debit_card', 'not_defined')),
    CONSTRAINT ck_payments_installments CHECK (installments BETWEEN 1 AND 24),
    CONSTRAINT ck_payments_amount       CHECK (amount >= 0)   -- 0 is valid for fully discounted vouchers
);

-- An order can receive more than one review, and a review id can span orders.
CREATE TABLE IF NOT EXISTS core.order_reviews (
    review_key          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_uid          CHAR(32)     NOT NULL,
    order_key           INTEGER      NOT NULL REFERENCES core.orders (order_key) ON DELETE CASCADE,
    score               SMALLINT     NOT NULL,
    comment_title       VARCHAR(100),
    comment_message     TEXT,
    review_created_at   TIMESTAMP    NOT NULL,
    review_answered_at  TIMESTAMP,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_reviews_review_order UNIQUE (review_uid, order_key),
    CONSTRAINT ck_reviews_uid   CHECK (review_uid ~ '^[0-9a-f]{32}$'),
    CONSTRAINT ck_reviews_score CHECK (score BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS ix_reviews_order ON core.order_reviews (order_key);


-- =============================================================================
-- OPERATIONS: pipeline audit trail and data-quality report
-- =============================================================================

CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id        INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        VARCHAR(10)  NOT NULL DEFAULT 'running',
    source        TEXT         NOT NULL,
    rows_read     INTEGER      CHECK (rows_read >= 0),
    rows_loaded   INTEGER      CHECK (rows_loaded >= 0),
    error_message TEXT,
    CONSTRAINT ck_runs_status   CHECK (status IN ('running', 'success', 'failed')),
    CONSTRAINT ck_runs_finished CHECK (finished_at IS NULL OR finished_at >= started_at)
);

-- Every problem the pipeline detects, with how many rows it affected and what
-- was done about it. The Data Quality Report is a query over this table.
CREATE TABLE IF NOT EXISTS ops.data_quality_issues (
    issue_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id         INTEGER      NOT NULL REFERENCES ops.pipeline_runs (run_id) ON DELETE CASCADE,
    source_table   VARCHAR(60)  NOT NULL,
    check_name     VARCHAR(80)  NOT NULL,
    category       VARCHAR(20)  NOT NULL,
    severity       VARCHAR(10)  NOT NULL,
    rows_affected  INTEGER      NOT NULL CHECK (rows_affected >= 0),
    action_taken   VARCHAR(20)  NOT NULL,
    details        JSONB,
    detected_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_dq_category CHECK (category IN (
        'missing', 'duplicate', 'invalid_value', 'invalid_date',
        'outlier', 'inconsistency', 'format', 'referential')),
    CONSTRAINT ck_dq_severity CHECK (severity IN ('info', 'warning', 'error')),
    CONSTRAINT ck_dq_action   CHECK (action_taken IN (
        'removed', 'imputed', 'corrected', 'nulled', 'flagged', 'kept'))
);

CREATE INDEX IF NOT EXISTS ix_dq_run ON ops.data_quality_issues (run_id);


-- =============================================================================
-- MACHINE LEARNING OUTPUTS
-- Every training run is recorded; each model has exactly one active run, which
-- is what the API, dashboard and AI analyst read (see analytics views).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS ml;

CREATE TABLE IF NOT EXISTS ml.model_runs (
    run_id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_name      VARCHAR(30)  NOT NULL,
    algorithm       VARCHAR(60)  NOT NULL,
    reference_date  DATE         NOT NULL,
    trained_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    params          JSONB,
    metrics         JSONB,
    notes           TEXT,
    is_active       BOOLEAN      NOT NULL DEFAULT FALSE,
    CONSTRAINT ck_model_runs_name CHECK (model_name IN (
        'rfm_segmentation', 'sales_forecast', 'churn', 'anomaly_detection'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_model_runs_active ON ml.model_runs (model_name) WHERE is_active;

-- Metrics of every candidate model evaluated in a run (model comparison tables).
CREATE TABLE IF NOT EXISTS ml.model_evaluations (
    run_id     INTEGER       NOT NULL REFERENCES ml.model_runs (run_id) ON DELETE CASCADE,
    candidate  VARCHAR(40)   NOT NULL,
    split      VARCHAR(30)   NOT NULL,
    metric     VARCHAR(30)   NOT NULL,
    value      NUMERIC(18, 6),
    PRIMARY KEY (run_id, candidate, split, metric)
);

CREATE TABLE IF NOT EXISTS ml.segment_definitions (
    run_id          INTEGER      NOT NULL REFERENCES ml.model_runs (run_id) ON DELETE CASCADE,
    segment         VARCHAR(12)  NOT NULL,
    display_order   SMALLINT     NOT NULL,
    rule            TEXT         NOT NULL,
    description     TEXT         NOT NULL,
    recommendation  TEXT         NOT NULL,
    PRIMARY KEY (run_id, segment)
);

CREATE TABLE IF NOT EXISTS ml.customer_segments (
    run_id        INTEGER         NOT NULL REFERENCES ml.model_runs (run_id) ON DELETE CASCADE,
    customer_key  INTEGER         NOT NULL REFERENCES core.customers (customer_key) ON DELETE CASCADE,
    recency_days  INTEGER         NOT NULL CHECK (recency_days >= 0),
    frequency     INTEGER         NOT NULL CHECK (frequency >= 1),
    monetary      NUMERIC(12, 2)  NOT NULL CHECK (monetary >= 0),
    r_score       SMALLINT        NOT NULL CHECK (r_score BETWEEN 1 AND 5),
    f_score       SMALLINT        NOT NULL CHECK (f_score BETWEEN 1 AND 5),
    m_score       SMALLINT        NOT NULL CHECK (m_score BETWEEN 1 AND 5),
    segment       VARCHAR(12)     NOT NULL CHECK (segment IN ('VIP', 'Loyal', 'Potential', 'At Risk', 'Lost')),
    PRIMARY KEY (run_id, customer_key)
);

CREATE INDEX IF NOT EXISTS ix_customer_segments_segment ON ml.customer_segments (run_id, segment);

CREATE TABLE IF NOT EXISTS ml.churn_scores (
    run_id             INTEGER        NOT NULL REFERENCES ml.model_runs (run_id) ON DELETE CASCADE,
    customer_key       INTEGER        NOT NULL REFERENCES core.customers (customer_key) ON DELETE CASCADE,
    churn_probability  NUMERIC(6, 5)  NOT NULL CHECK (churn_probability BETWEEN 0 AND 1),
    risk_band          VARCHAR(6)     NOT NULL CHECK (risk_band IN ('High', 'Medium', 'Low')),
    top_factors        JSONB          NOT NULL,
    PRIMARY KEY (run_id, customer_key)
);

CREATE INDEX IF NOT EXISTS ix_churn_scores_probability ON ml.churn_scores (run_id, churn_probability DESC);

CREATE TABLE IF NOT EXISTS ml.sales_forecast (
    run_id         INTEGER         NOT NULL REFERENCES ml.model_runs (run_id) ON DELETE CASCADE,
    forecast_date  DATE            NOT NULL,
    forecast       NUMERIC(14, 2)  NOT NULL,
    lower_80       NUMERIC(14, 2)  NOT NULL,
    upper_80       NUMERIC(14, 2)  NOT NULL,
    PRIMARY KEY (run_id, forecast_date),
    CONSTRAINT ck_forecast_interval CHECK (lower_80 <= forecast AND forecast <= upper_80)
);

CREATE TABLE IF NOT EXISTS ml.anomalies (
    anomaly_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        INTEGER         NOT NULL REFERENCES ml.model_runs (run_id) ON DELETE CASCADE,
    entity_type   VARCHAR(12)     NOT NULL,
    entity_key    INTEGER,                         -- product_key / customer_key when applicable
    period_start  DATE            NOT NULL,
    period_end    DATE            NOT NULL,
    metric        VARCHAR(30)     NOT NULL,
    observed      NUMERIC(14, 2),
    expected      NUMERIC(14, 2),
    score         NUMERIC(10, 3)  NOT NULL,
    direction     VARCHAR(5)      NOT NULL,
    method        VARCHAR(30)     NOT NULL,
    description   TEXT            NOT NULL,
    CONSTRAINT ck_anomalies_entity    CHECK (entity_type IN ('day', 'product', 'customer')),
    CONSTRAINT ck_anomalies_direction CHECK (direction IN ('up', 'down', 'mixed')),
    CONSTRAINT ck_anomalies_period    CHECK (period_end >= period_start)
);

CREATE INDEX IF NOT EXISTS ix_anomalies_run ON ml.anomalies (run_id, entity_type);


-- =============================================================================
-- updated_at triggers
-- =============================================================================

CREATE OR REPLACE TRIGGER trg_locations_updated_at  BEFORE UPDATE ON core.locations
    FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
CREATE OR REPLACE TRIGGER trg_categories_updated_at BEFORE UPDATE ON core.categories
    FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
CREATE OR REPLACE TRIGGER trg_products_updated_at   BEFORE UPDATE ON core.products
    FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
CREATE OR REPLACE TRIGGER trg_sellers_updated_at    BEFORE UPDATE ON core.sellers
    FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
CREATE OR REPLACE TRIGGER trg_customers_updated_at  BEFORE UPDATE ON core.customers
    FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
CREATE OR REPLACE TRIGGER trg_orders_updated_at     BEFORE UPDATE ON core.orders
    FOR EACH ROW EXECUTE FUNCTION core.set_updated_at();
