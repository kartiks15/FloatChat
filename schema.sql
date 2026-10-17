-- ============================================================
-- FloatChat — ARGO PostgreSQL Schema
-- ============================================================

-- Idempotent bootstrap/migration. Existing measurements are preserved.

-- ── floats ────────────────────────────────────────────────
-- One row per physical ARGO float (WMO number)
CREATE TABLE IF NOT EXISTS floats (
    wmo_id          VARCHAR(16)  PRIMARY KEY,
    platform_type   VARCHAR(64),
    dac             VARCHAR(64),         -- Data Assembly Centre (e.g. CORIOLIS, AOML)
    project_name    VARCHAR(128),
    pi_name         VARCHAR(128),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ── profiles ──────────────────────────────────────────────
-- One row per float surfacing event (dive cycle)
CREATE TABLE IF NOT EXISTS profiles (
    profile_id      SERIAL       PRIMARY KEY,
    wmo_id          VARCHAR(16)  NOT NULL REFERENCES floats(wmo_id),
    cycle_number    INTEGER      NOT NULL,
    direction       CHAR(1),              -- 'A' ascent / 'D' descent
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    juld            DOUBLE PRECISION,     -- Julian day (raw from NetCDF)
    date_utc        TIMESTAMP,            -- Parsed UTC datetime
    positioning_system VARCHAR(16),
    data_mode       CHAR(1),              -- 'R' real-time / 'D' delayed / 'A' adjusted
    source_file     VARCHAR(256),
    loaded_at       TIMESTAMP DEFAULT NOW(),
    UNIQUE (wmo_id, cycle_number, direction)
);

CREATE INDEX IF NOT EXISTS idx_profiles_wmo        ON profiles(wmo_id);
CREATE INDEX IF NOT EXISTS idx_profiles_date       ON profiles(date_utc);
CREATE INDEX IF NOT EXISTS idx_profiles_latlon     ON profiles(latitude, longitude);

-- ── observations ──────────────────────────────────────────
-- One row per depth level within a profile
-- Core variables: PRES, TEMP, PSAL
-- BGC variables stored as nullable columns (populated only for BGC floats)
CREATE TABLE IF NOT EXISTS observations (
    obs_id          BIGSERIAL    PRIMARY KEY,
    profile_id      INTEGER      NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
    wmo_id          VARCHAR(16)  NOT NULL,   -- denormalised for fast queries
    date_utc        TIMESTAMP    NOT NULL,   -- denormalised from profiles
    latitude        DOUBLE PRECISION,        -- denormalised from profiles
    longitude       DOUBLE PRECISION,        -- denormalised from profiles
    depth_level     INTEGER      NOT NULL,   -- index within the profile (0-based)
    pres            DOUBLE PRECISION,        -- Pressure [dbar]
    pres_qc         SMALLINT,
    temp            DOUBLE PRECISION,        -- Temperature [°C]
    temp_qc         SMALLINT,
    psal            DOUBLE PRECISION,        -- Practical Salinity [PSU]
    psal_qc         SMALLINT,
    -- BGC parameters (NULL for core floats)
    doxy            DOUBLE PRECISION,        -- Dissolved Oxygen [μmol/kg]
    doxy_qc         SMALLINT,
    chla            DOUBLE PRECISION,        -- Chlorophyll-a [mg/m³]
    chla_qc         SMALLINT,
    nitrate         DOUBLE PRECISION,        -- Nitrate [μmol/kg]
    nitrate_qc      SMALLINT,
    bbp700          DOUBLE PRECISION,        -- Particle backscattering 700nm [m⁻¹]
    bbp700_qc       SMALLINT
);

CREATE INDEX IF NOT EXISTS idx_obs_profile   ON observations(profile_id);
CREATE INDEX IF NOT EXISTS idx_obs_wmo       ON observations(wmo_id);
CREATE INDEX IF NOT EXISTS idx_obs_date      ON observations(date_utc);
CREATE INDEX IF NOT EXISTS idx_obs_latlon    ON observations(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_obs_pres      ON observations(pres);

-- Preserve independent core/BGC grids and replace only the same source on re-import.
ALTER TABLE observations ADD COLUMN IF NOT EXISTS source_file VARCHAR(256) NOT NULL DEFAULT 'legacy';
ALTER TABLE observations ADD COLUMN IF NOT EXISTS source_profile INTEGER NOT NULL DEFAULT 0;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS source_rank SMALLINT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_obs_source ON observations(profile_id, source_file, source_profile);

-- ── Helpful view: profile summary ─────────────────────────
CREATE OR REPLACE VIEW profile_summary AS
SELECT
    p.profile_id,
    p.wmo_id,
    p.cycle_number,
    p.date_utc,
    p.latitude,
    p.longitude,
    p.data_mode,
    COUNT(o.obs_id)          AS n_levels,
    MIN(o.pres)              AS pres_min,
    MAX(o.pres)              AS pres_max,
    MIN(o.temp)              AS temp_min,
    MAX(o.temp)              AS temp_max,
    MIN(o.psal)              AS psal_min,
    MAX(o.psal)              AS psal_max,
    BOOL_OR(o.doxy IS NOT NULL) AS has_doxy,
    BOOL_OR(o.chla IS NOT NULL) AS has_chla
FROM profiles p
LEFT JOIN observations o ON o.profile_id = p.profile_id
GROUP BY p.profile_id, p.wmo_id, p.cycle_number, p.date_utc,
         p.latitude, p.longitude, p.data_mode;
