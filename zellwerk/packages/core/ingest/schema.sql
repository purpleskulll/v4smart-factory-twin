-- =========================================================================
-- zellwerk — Datenmodell (SPEC §6.2)
--
-- Zeitreihen UND semantisches Modell liegen bewusst in EINER Datenbank: die
-- Genealogie-Abfragen der MCP-Tools verbinden Stammdaten mit Messwerten, und
-- ein Join über zwei Systeme hinweg wäre der teuerste Teil jeder Anfrage.
-- =========================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ------------------------------------------------------------- Stammdaten --

CREATE TABLE IF NOT EXISTS asset (
    id            TEXT PRIMARY KEY,
    typ           TEXT NOT NULL,
    site          TEXT NOT NULL,
    area          TEXT NOT NULL,
    line          TEXT NOT NULL,
    opc_endpoint  TEXT,
    status        TEXT NOT NULL DEFAULT 'unbekannt',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS production_order (
    id           TEXT PRIMARY KEY,
    produkt      TEXT NOT NULL,
    sollmenge    INTEGER NOT NULL,
    status       TEXT NOT NULL,
    erstellt_am  TIMESTAMPTZ,
    faellig_am   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS lot (
    id          TEXT PRIMARY KEY,
    order_id    TEXT REFERENCES production_order(id),
    station     TEXT NOT NULL,
    material    TEXT NOT NULL,
    parent_id   TEXT,
    start_ts    TIMESTAMPTZ NOT NULL,
    end_ts      TIMESTAMPTZ,
    -- Qualitätsmerkmale, die das Los an die nächste Stufe weitergibt.
    -- Hier liegt die Evidenz, mit der ein Agent eine Ursache belegt.
    traits      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS lot_parent_idx  ON lot(parent_id);
CREATE INDEX IF NOT EXISTS lot_station_idx ON lot(station);

CREATE TABLE IF NOT EXISTS cell (
    serial      TEXT PRIMARY KEY,
    lot_id      TEXT REFERENCES lot(id),
    status      TEXT NOT NULL DEFAULT 'in_prozess',
    grade       TEXT,
    created_at  TIMESTAMPTZ NOT NULL,
    traits      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS cell_lot_idx    ON cell(lot_id);
CREATE INDEX IF NOT EXISTS cell_status_idx ON cell(status);

CREATE TABLE IF NOT EXISTS genealogy (
    id           BIGSERIAL PRIMARY KEY,
    parent_kind  TEXT NOT NULL,
    parent_id    TEXT NOT NULL,
    child_kind   TEXT NOT NULL,
    child_id     TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (parent_kind, parent_id, child_kind, child_id)
);
CREATE INDEX IF NOT EXISTS genealogy_child_idx  ON genealogy(child_kind, child_id);
CREATE INDEX IF NOT EXISTS genealogy_parent_idx ON genealogy(parent_kind, parent_id);

-- -------------------------------------------------------------- Zeitreihen --

CREATE TABLE IF NOT EXISTS measurement (
    ts        TIMESTAMPTZ NOT NULL,
    asset_id  TEXT NOT NULL,
    name      TEXT NOT NULL,
    value     DOUBLE PRECISION,
    text_value TEXT,          -- für nicht-numerische PVs (z. B. "pass"/"fail")
    unit      TEXT,
    quality   TEXT NOT NULL DEFAULT 'good'
);
SELECT create_hypertable('measurement', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS measurement_lookup_idx ON measurement(asset_id, name, ts DESC);

CREATE TABLE IF NOT EXISTS event (
    ts        TIMESTAMPTZ NOT NULL,
    asset_id  TEXT,
    severity  TEXT NOT NULL,          -- info | warn | alarm
    code      TEXT NOT NULL,
    payload   JSONB NOT NULL DEFAULT '{}'::jsonb,
    acked     BOOLEAN NOT NULL DEFAULT FALSE
);
SELECT create_hypertable('event', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS event_lookup_idx ON event(asset_id, ts DESC);
CREATE INDEX IF NOT EXISTS event_open_idx   ON event(acked, ts DESC);

-- ------------------------------------------------------------------ Audit --
-- Jeder Tool-Aufruf eines Agenten landet hier (SPEC §9). Ohne lückenloses
-- Audit-Log ist "Shadow Mode" eine Behauptung statt einer Eigenschaft.

CREATE TABLE IF NOT EXISTS action_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor       TEXT NOT NULL,          -- agent | rule | mensch
    tool        TEXT NOT NULL,
    params      JSONB NOT NULL DEFAULT '{}'::jsonb,
    mode        TEXT NOT NULL,          -- shadow | live
    ergebnis    JSONB,
    begruendung TEXT
);
CREATE INDEX IF NOT EXISTS action_log_ts_idx ON action_log(ts DESC);

-- ------------------------------------------------------- Sollwertfenster ---
-- Aus SPEC §7.1. Liegt in der DB, damit `get_process_window` (§9) und die
-- Grafana-Panels dieselbe Wahrheit benutzen wie die Simulation.

CREATE TABLE IF NOT EXISTS process_window (
    station   TEXT NOT NULL,
    name      TEXT NOT NULL,
    min_value DOUBLE PRECISION,
    max_value DOUBLE PRECISION,
    unit      TEXT,
    PRIMARY KEY (station, name)
);

INSERT INTO process_window (station, name, min_value, max_value, unit) VALUES
    ('mixer01',     'viskositaet_pas',           2.0,    6.0,   'Pa*s'),
    ('mixer01',     'feststoffanteil_pct',      45.0,   55.0,   '%'),
    ('mixer01',     'mixer_temp_c',             20.0,   30.0,   'degC'),
    ('coater01',    'nassschichtdicke_um',     120.0,  200.0,   'um'),
    ('coater01',    'bahngeschwindigkeit_m_min',20.0,   60.0,   'm/min'),
    ('coater01',    'trocknertemp_c',           80.0,  130.0,   'degC'),
    ('coater01',    'flaechengewicht_g_m2',    140.0,  180.0,   'g/m2'),
    ('calender01',  'liniendruck_n_mm',        300.0, 1500.0,   'N/mm'),
    ('calender01',  'porositaet_pct',           28.0,   38.0,   '%'),
    ('assembly01',  'ausrichtungsfehler_um',     0.0,  300.0,   'um'),
    ('assembly01',  'zugspannung_n',             8.0,   14.0,   'N'),
    ('filling01',   'dosiermenge_g',           4.925,  5.075,   'g'),
    ('filling01',   'vakuumdruck_mbar',          0.5,    5.0,   'mbar'),
    ('formation01', 'spannung_v',                3.0,    4.2,   'V'),
    ('formation01', 'temp_c',                   25.0,   45.0,   'degC'),
    ('formation01', 'kapazitaet_ah',             4.6,    5.4,   'Ah')
ON CONFLICT (station, name) DO NOTHING;

INSERT INTO asset (id, typ, site, area, line, opc_endpoint) VALUES
    ('mixer01',     'mixer',     'werk1', 'elektrode', 'linie1', 'opc.tcp://simfactory:4841/zellwerk/mixer01'),
    ('coater01',    'coater',    'werk1', 'elektrode', 'linie1', 'opc.tcp://simfactory:4842/zellwerk/coater01'),
    ('calender01',  'calender',  'werk1', 'elektrode', 'linie1', 'opc.tcp://simfactory:4843/zellwerk/calender01'),
    ('assembly01',  'assembly',  'werk1', 'zelle',     'linie1', 'opc.tcp://simfactory:4844/zellwerk/assembly01'),
    ('filling01',   'filling',   'werk1', 'zelle',     'linie1', 'opc.tcp://simfactory:4845/zellwerk/filling01'),
    ('formation01', 'formation', 'werk1', 'zelle',     'linie1', 'opc.tcp://simfactory:4846/zellwerk/formation01')
ON CONFLICT (id) DO NOTHING;
