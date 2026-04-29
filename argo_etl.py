"""
FloatChat — ARGO NetCDF → PostgreSQL ETL Pipeline
==================================================
Handles both:
  • Core Argo profiles  (PRES / TEMP / PSAL)
  • BGC-Argo profiles   (+ DOXY / CHLA / NITRATE / BBP700)

Usage
-----
Single file:
    python argo_etl.py --file /data/argo/4902915_001.nc

Directory (recursive):
    python argo_etl.py --dir /data/argo/indian_ocean/

With schema reset:
    python argo_etl.py --dir /data/argo/ --reset-schema

Environment variables (or .env file):
    DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT
"""

import os
import glob
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import psycopg2
import psycopg2.extras
from netCDF4 import Dataset
from dotenv import load_dotenv

# ── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("argo_etl")

load_dotenv()

# ── DB config from env ─────────────────────────────────────
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME", "floatchat"),
    "user":   os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASS", ""),
    "host":   os.getenv("DB_HOST", "localhost"),
    "port":   os.getenv("DB_PORT", "5432"),
}

# ── Constants ──────────────────────────────────────────────
JULD_ORIGIN = datetime(1950, 1, 1, tzinfo=timezone.utc)   # ARGO Julian day epoch
QC_GOOD     = {1, 2}                                        # QC flags to retain (1=good, 2=probably good)
FILL_FLOAT  = 99999.0                                       # ARGO fill value for floats
BATCH_SIZE  = 500                                           # rows per executemany call


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def apply_schema(conn, schema_path: str):
    """Drop and re-create all tables from schema.sql."""
    with open(schema_path) as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    log.info("Schema applied from %s", schema_path)


def juld_to_datetime(juld: float) -> Optional[datetime]:
    """Convert ARGO Julian day (days since 1950-01-01) to UTC datetime."""
    if juld is None or np.isnan(juld) or abs(juld) > 1e10:
        return None
    try:
        delta_seconds = int(round(juld * 86400))
        return datetime(1950, 1, 1, tzinfo=timezone.utc).replace(
            tzinfo=None
        ) + __import__("datetime").timedelta(seconds=delta_seconds)
    except (OverflowError, ValueError):
        return None


def clean_float(val) -> Optional[float]:
    """Return None for NaN / ARGO fill values / masked elements, else float."""
    if val is None:
        return None
    if hasattr(val, "mask") and np.ma.is_masked(val):
        return None
    try:
        f = float(val.item() if hasattr(val, "item") else val)
    except (ValueError, TypeError):
        return None
    if np.isnan(f) or abs(f) >= FILL_FLOAT:
        return None
    return f


def clean_qc(val) -> Optional[int]:
    """Parse QC byte/char to int, None on error or if masked."""
    if val is None:
        return None
    # numpy masked scalars: check before calling int()
    if hasattr(val, "mask") and np.ma.is_masked(val):
        return None
    try:
        v = val.item() if hasattr(val, "item") else val
        return int(v)
    except (ValueError, TypeError):
        return None


def read_nc_var(nc: Dataset, name: str, index: int = 0):
    """
    Safely read a variable from a NetCDF dataset.
    For 2-D profile arrays, returns the slice at `index`.
    Returns None if variable doesn't exist.
    """
    if name not in nc.variables:
        return None
    var = nc.variables[name]
    data = var[:]  # masked array
    if data.ndim == 1:
        return data
    if data.ndim == 2:
        return data[index, :]
    return None


def read_scalar(nc: Dataset, name: str, index: int = 0):
    """Read a scalar or 1-D variable element."""
    if name not in nc.variables:
        return None
    var = nc.variables[name]
    raw = var[:]
    if raw.ndim == 0:
        val = raw.item()
    elif raw.ndim == 1:
        val = raw[index]
    else:
        val = raw[index, 0] if raw.shape[1] >= 1 else None
    # decode bytes
    if isinstance(val, (bytes, np.bytes_)):
        val = val.decode("utf-8", errors="ignore").strip()
    if hasattr(val, "item"):
        val = val.item()
    return val if val not in ("", None) else None


def decode_str_var(nc: Dataset, name: str, index: int = 0) -> str:
    """Read a char array (N_PROF × N_PARAM × STRING_LEN) into a clean string."""
    if name not in nc.variables:
        return ""
    var = nc.variables[name]
    raw = var[:]
    if raw.ndim == 1:
        chunk = raw
    elif raw.ndim == 2:
        chunk = raw[index]
    elif raw.ndim == 3:
        chunk = raw[index, 0]
    else:
        return ""
    decoded = b"".join(
        (b if isinstance(b, bytes) else bytes([b])) for b in chunk.filled(b" ")
    ).decode("utf-8", errors="ignore").strip()
    return decoded


# ──────────────────────────────────────────────────────────
# Float upsert
# ──────────────────────────────────────────────────────────

def upsert_float(cur, wmo_id: str, nc: Dataset):
    """Insert or update the floats table for this WMO ID."""
    platform_type = decode_str_var(nc, "PLATFORM_TYPE") or ""
    dac           = decode_str_var(nc, "DATA_CENTRE")   or ""
    project_name  = decode_str_var(nc, "PROJECT_NAME")  or ""
    pi_name       = decode_str_var(nc, "PI_NAME")       or ""

    cur.execute(
        """
        INSERT INTO floats (wmo_id, platform_type, dac, project_name, pi_name)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (wmo_id) DO UPDATE
            SET platform_type = EXCLUDED.platform_type,
                dac           = EXCLUDED.dac,
                project_name  = EXCLUDED.project_name,
                pi_name       = EXCLUDED.pi_name
        """,
        (wmo_id, platform_type[:64], dac[:64], project_name[:128], pi_name[:128]),
    )


# ──────────────────────────────────────────────────────────
# Profile + observation ingestion
# ──────────────────────────────────────────────────────────

def ingest_profile(cur, wmo_id: str, nc: Dataset, prof_idx: int, source_file: str) -> Optional[int]:
    """
    Parse one profile from the NetCDF file and insert into `profiles`.
    Returns the new profile_id, or None if already exists / bad data.
    """
    cycle_number = read_scalar(nc, "CYCLE_NUMBER", prof_idx)
    direction    = read_scalar(nc, "DIRECTION",    prof_idx)
    latitude     = clean_float(read_scalar(nc, "LATITUDE",  prof_idx))
    longitude    = clean_float(read_scalar(nc, "LONGITUDE", prof_idx))
    juld_raw     = read_scalar(nc, "JULD", prof_idx)
    data_mode    = read_scalar(nc, "DATA_MODE", prof_idx)
    pos_system   = decode_str_var(nc, "POSITIONING_SYSTEM", prof_idx) or None

    if cycle_number is None:
        log.debug("  Skipping profile %d — missing CYCLE_NUMBER", prof_idx)
        return None

    try:
        cycle_number = int(cycle_number)
    except (TypeError, ValueError):
        return None

    date_utc = juld_to_datetime(float(juld_raw)) if juld_raw is not None else None

    try:
        cur.execute(
            """
            INSERT INTO profiles
                (wmo_id, cycle_number, direction, latitude, longitude,
                 juld, date_utc, positioning_system, data_mode, source_file)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (wmo_id, cycle_number, direction) DO NOTHING
            RETURNING profile_id
            """,
            (
                wmo_id,
                cycle_number,
                str(direction)[0] if direction else None,
                latitude,
                longitude,
                float(juld_raw) if juld_raw is not None else None,
                date_utc,
                pos_system[:16] if pos_system else None,
                str(data_mode)[0] if data_mode else None,
                source_file[:256],
            ),
        )
        row = cur.fetchone()
        return row[0] if row else None
    except psycopg2.Error as e:
        log.warning("  Profile insert failed (wmo=%s cycle=%s): %s", wmo_id, cycle_number, e)
        return None


def ingest_observations(cur, profile_id: int, wmo_id: str,
                         date_utc, latitude, longitude,
                         nc: Dataset, prof_idx: int):
    """Parse all depth levels for one profile and bulk-insert into observations."""

    # ── Read all variables for this profile ───────────────
    def get_arr(name):
        arr = read_nc_var(nc, name, prof_idx)
        return arr if arr is not None else None

    pres      = get_arr("PRES");     pres_qc  = get_arr("PRES_QC")
    temp      = get_arr("TEMP");     temp_qc  = get_arr("TEMP_QC")
    psal      = get_arr("PSAL");     psal_qc  = get_arr("PSAL_QC")

    # BGC (may not exist in core files)
    doxy      = get_arr("DOXY");     doxy_qc  = get_arr("DOXY_QC")
    chla      = get_arr("CHLA");     chla_qc  = get_arr("CHLA_QC")
    nitrate   = get_arr("NITRATE");  nitrate_qc = get_arr("NITRATE_QC")
    bbp700    = get_arr("BBP700");   bbp700_qc  = get_arr("BBP700_QC")

    if pres is None or len(pres) == 0:
        return 0

    n_levels = len(pres)
    batch = []

    for i in range(n_levels):
        def fval(arr, idx=i):
            return clean_float(arr[idx]) if arr is not None and idx < len(arr) else None

        def qval(arr, idx=i):
            return clean_qc(arr[idx]) if arr is not None and idx < len(arr) else None

        pres_val = fval(pres)
        if pres_val is None:
            continue  # skip entirely missing pressure levels

        batch.append((
            profile_id, wmo_id, date_utc, latitude, longitude, i,
            pres_val,    qval(pres_qc),
            fval(temp),  qval(temp_qc),
            fval(psal),  qval(psal_qc),
            fval(doxy),  qval(doxy_qc),
            fval(chla),  qval(chla_qc),
            fval(nitrate), qval(nitrate_qc),
            fval(bbp700),  qval(bbp700_qc),
        ))

        # Flush batch
        if len(batch) >= BATCH_SIZE:
            _insert_obs_batch(cur, batch)
            batch = []

    if batch:
        _insert_obs_batch(cur, batch)

    return n_levels


def _insert_obs_batch(cur, batch: list):
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO observations
            (profile_id, wmo_id, date_utc, latitude, longitude, depth_level,
             pres, pres_qc, temp, temp_qc, psal, psal_qc,
             doxy, doxy_qc, chla, chla_qc, nitrate, nitrate_qc,
             bbp700, bbp700_qc)
        VALUES %s
        ON CONFLICT DO NOTHING
        """,
        batch,
        page_size=BATCH_SIZE,
    )


# ──────────────────────────────────────────────────────────
# Main file processor
# ──────────────────────────────────────────────────────────

def process_file(conn, nc_path: str) -> dict:
    """
    Parse one .nc file and load all its profiles + observations.
    Returns a stats dict.
    """
    stats = {"file": nc_path, "profiles": 0, "obs": 0, "skipped": 0, "error": None}

    try:
        nc = Dataset(nc_path, "r")
    except Exception as e:
        stats["error"] = str(e)
        log.error("Cannot open %s: %s", nc_path, e)
        return stats

    try:
        # ── WMO ID ────────────────────────────────────────
        wmo_id = None
        if "PLATFORM_NUMBER" in nc.variables:
            raw = nc.variables["PLATFORM_NUMBER"][:]
            if raw.ndim >= 2:
                wmo_id = b"".join(
                    b if isinstance(b, bytes) else bytes([b])
                    for b in raw[0].filled(b" ")
                ).decode("utf-8", errors="ignore").strip()
            elif raw.ndim == 1:
                wmo_id = b"".join(
                    b if isinstance(b, bytes) else bytes([b])
                    for b in raw.filled(b" ")
                ).decode("utf-8", errors="ignore").strip()

        if not wmo_id:
            # Fallback: try to parse from filename (e.g. 4902915_001.nc)
            wmo_id = Path(nc_path).stem.split("_")[0]

        wmo_id = wmo_id[:16]
        n_prof = nc.dimensions["N_PROF"].size if "N_PROF" in nc.dimensions else 1

        log.info("Processing  %s  (WMO=%s, %d profile(s))", Path(nc_path).name, wmo_id, n_prof)

        with conn.cursor() as cur:
            upsert_float(cur, wmo_id, nc)

            for pi in range(n_prof):
                profile_id = ingest_profile(cur, wmo_id, nc, pi, nc_path)

                if profile_id is None:
                    stats["skipped"] += 1
                    continue

                # Retrieve lat/lon/date for denormalisation
                lat  = clean_float(read_scalar(nc, "LATITUDE",  pi))
                lon  = clean_float(read_scalar(nc, "LONGITUDE", pi))
                juld = read_scalar(nc, "JULD", pi)
                date_utc = juld_to_datetime(float(juld)) if juld is not None else None

                n_obs = ingest_observations(
                    cur, profile_id, wmo_id, date_utc, lat, lon, nc, pi
                )
                stats["profiles"] += 1
                stats["obs"]      += n_obs

        conn.commit()

    except Exception as e:
        conn.rollback()
        stats["error"] = str(e)
        log.error("Error in %s: %s", nc_path, e, exc_info=True)
    finally:
        nc.close()

    return stats


# ──────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ARGO NetCDF → PostgreSQL ETL")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Single .nc file to ingest")
    group.add_argument("--dir",  help="Directory to scan recursively for .nc files")
    parser.add_argument(
        "--reset-schema",
        action="store_true",
        help="Drop & recreate all tables before loading (destructive!)",
    )
    parser.add_argument(
        "--schema",
        default=str(Path(__file__).parent / "schema.sql"),
        help="Path to schema.sql (default: sibling schema.sql)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of files to process (useful for testing)",
    )
    args = parser.parse_args()

    # ── Collect files ─────────────────────────────────────
    if args.file:
        files = [args.file]
    else:
        files = sorted(glob.glob(os.path.join(args.dir, "**", "*.nc"), recursive=True))
        log.info("Found %d .nc files in %s", len(files), args.dir)

    if args.limit:
        files = files[: args.limit]
        log.info("Limiting to %d files", len(files))

    if not files:
        log.error("No .nc files found. Check your --file / --dir argument.")
        return

    # ── Connect & optionally reset schema ─────────────────
    log.info("Connecting to database '%s' @ %s:%s", DB_CONFIG["dbname"], DB_CONFIG["host"], DB_CONFIG["port"])
    conn = get_connection()

    if args.reset_schema:
        log.warning("Resetting schema — all existing data will be dropped!")
        apply_schema(conn, args.schema)

    # ── Process files ─────────────────────────────────────
    total = {"profiles": 0, "obs": 0, "skipped": 0, "errors": 0}
    for i, fpath in enumerate(files, 1):
        log.info("[%d/%d] %s", i, len(files), fpath)
        stats = process_file(conn, fpath)
        total["profiles"] += stats["profiles"]
        total["obs"]      += stats["obs"]
        total["skipped"]  += stats["skipped"]
        if stats["error"]:
            total["errors"] += 1

    conn.close()

    log.info("=" * 55)
    log.info("ETL complete:")
    log.info("  Files processed : %d", len(files))
    log.info("  Profiles loaded : %d", total["profiles"])
    log.info("  Observations    : %d", total["obs"])
    log.info("  Skipped profiles: %d", total["skipped"])
    log.info("  Files w/ errors : %d", total["errors"])
    log.info("=" * 55)


if __name__ == "__main__":
    main()