"""ARGO NetCDF ingestion with QC, provenance, atomic files and repeatable imports.

Use Sprof OR separate core + BGC files per float to avoid overlapping observations.
Independent grids are retained separately; they must never be joined by depth index.
"""
import argparse
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import psycopg2
from psycopg2.extras import execute_values
from netCDF4 import Dataset
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)
QC_GOOD = {1, 2}
# A re-import only moves a profile's mode up (R < A < D), so a real-time file never undoes delayed data.
MODE_AT_LEAST = ("COALESCE(array_position(ARRAY['R','A','D'], EXCLUDED.data_mode::text), 0) >= "
                 "COALESCE(array_position(ARRAY['R','A','D'], profiles.data_mode::text), 0)")
PARAMETERS = ("PRES", "TEMP", "PSAL", "DOXY", "CHLA", "NITRATE", "BBP700")


def get_connection():
    password = os.getenv("DB_WRITE_PASS") or os.getenv("DB_PASS")
    if not password:
        raise ValueError("Set DB_WRITE_PASS (or DB_PASS) for ingestion.")
    return psycopg2.connect(dbname=os.getenv("DB_NAME", "floatchat"),
        user=os.getenv("DB_WRITE_USER", os.getenv("DB_USER", "floatchat_admin")),
        password=password, host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"), connect_timeout=10)


def apply_schema(conn, schema_path):
    with conn.cursor() as cur:
        cur.execute(Path(schema_path).read_text(encoding="utf-8"))
    conn.commit()


def refresh_summary(conn):
    """Recompute the materialized profile_summary. Returns False if schema.sql is not applied yet."""
    with conn.cursor() as cur:
        cur.execute("SELECT relkind, relispopulated FROM pg_class WHERE oid = to_regclass('profile_summary')")
        row = cur.fetchone()
        if not row or row[0] != "m":
            conn.rollback()
            log.warning("profile_summary is not a materialized view; apply schema.sql to enable it.")
            return False
        # CONCURRENTLY lets the dashboard keep reading the old summary while this runs.
        cur.execute("REFRESH MATERIALIZED VIEW " + ("CONCURRENTLY " if row[1] else "") + "profile_summary")
    conn.commit()
    return True


def clean_float(value):
    if value is None or np.ma.is_masked(value):
        return None
    try:
        value = float(value)
        return value if np.isfinite(value) and abs(value) < 99999 else None
    except (TypeError, ValueError):
        return None


def clean_qc(value):
    if value is None or np.ma.is_masked(value):
        return None
    try:
        value = int(value)
        return value if 0 <= value <= 9 else None
    except (TypeError, ValueError):
        return None


def decode(value):
    if value is None or (np.ndim(value) == 0 and np.ma.is_masked(value)):
        return ""
    array = np.ma.asarray(value)
    fill = b" " if array.dtype.kind == "S" else " "
    values = array.filled(fill).flatten()
    return "".join(v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
                   for v in values).replace("\x00", "").strip()


def read_scalar(nc, name, index=0):
    if name not in nc.variables:
        return None
    var = nc.variables[name]
    value = var[index] if "N_PROF" in var.dimensions else var[...]
    if np.size(value) != 1:
        return None
    value = np.ma.asarray(value).reshape(-1)[0]
    if np.ma.is_masked(value):
        return None
    return decode(value) if isinstance(value, (bytes, np.bytes_)) else value.item() if hasattr(value, "item") else value


def decode_str_var(nc, name, index=0):
    if name not in nc.variables:
        return ""
    var = nc.variables[name]
    return decode(var[index] if "N_PROF" in var.dimensions else var[...])


def juld_to_datetime(value):
    value = clean_float(value)
    if value is None:
        return None
    try:
        return datetime(1950, 1, 1) + timedelta(days=value)
    except (OverflowError, ValueError):
        return None


def parameter_mode(nc, name, index):
    if "STATION_PARAMETERS" in nc.variables and "PARAMETER_DATA_MODE" in nc.variables:
        names = nc.variables["STATION_PARAMETERS"][index]
        modes = nc.variables["PARAMETER_DATA_MODE"][index]
        for i, value in enumerate(names):
            if decode(value) == name:
                return decode(modes[i])
    return read_scalar(nc, "DATA_MODE", index)


def read_parameter(nc, name, index):
    selected = name
    if parameter_mode(nc, name, index) in ("A", "D") and name + "_ADJUSTED" in nc.variables:
        selected += "_ADJUSTED"
    if selected not in nc.variables:
        return None, None
    values = nc.variables[selected][index, :]
    qc_name = selected + "_QC"
    qc = nc.variables[qc_name][index, :] if qc_name in nc.variables else None
    return values, qc


def source_key(path):
    """R/D versions share a key; core, BGC and synthetic grids stay separate."""
    name = Path(path).name
    return re.sub(r"^([BS]?)[RD](?=\d)", lambda m: (m[1] or "C"), name)[:256]


def process_file(conn, nc_path):
    stats = {"file": str(nc_path), "profiles": 0, "obs": 0, "skipped": 0, "error": None}
    try:
        with Dataset(nc_path) as nc, conn.cursor() as cur:
            wmo = decode_str_var(nc, "PLATFORM_NUMBER")
            if not re.fullmatch(r"\d{5,8}", wmo):
                raise ValueError("Missing or invalid PLATFORM_NUMBER")
            cur.execute("""INSERT INTO floats(wmo_id,platform_type,dac,project_name,pi_name)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(wmo_id) DO UPDATE SET
                platform_type=EXCLUDED.platform_type, dac=EXCLUDED.dac,
                project_name=EXCLUDED.project_name, pi_name=EXCLUDED.pi_name""",
                [wmo, decode_str_var(nc,"PLATFORM_TYPE")[:64], decode_str_var(nc,"DATA_CENTRE")[:64],
                 decode_str_var(nc,"PROJECT_NAME")[:128], decode_str_var(nc,"PI_NAME")[:128]])
            for index in range(len(nc.dimensions["N_PROF"])):
                cycle = read_scalar(nc, "CYCLE_NUMBER", index)
                direction = read_scalar(nc, "DIRECTION", index)
                date_utc = juld_to_datetime(read_scalar(nc, "JULD", index))
                if cycle is None or direction not in ("A", "D") or date_utc is None:
                    stats["skipped"] += 1
                    continue
                lat = clean_float(read_scalar(nc, "LATITUDE", index))
                lon = clean_float(read_scalar(nc, "LONGITUDE", index))
                lat = lat if lat is not None and -90 <= lat <= 90 else None
                lon = lon if lon is not None and -180 <= lon <= 180 else None
                mode = read_scalar(nc, "DATA_MODE", index)
                mode = mode if mode in ("R", "A", "D") else None
                cur.execute("""INSERT INTO profiles(wmo_id,cycle_number,direction,latitude,longitude,
                    juld,date_utc,positioning_system,data_mode,source_file)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(wmo_id,cycle_number,direction) DO UPDATE SET
                    latitude=COALESCE(profiles.latitude,EXCLUDED.latitude),
                    longitude=COALESCE(profiles.longitude,EXCLUDED.longitude),
                    data_mode=CASE WHEN {better} THEN EXCLUDED.data_mode ELSE profiles.data_mode END,
                    source_file=CASE WHEN {better} THEN EXCLUDED.source_file ELSE profiles.source_file END
                    RETURNING profile_id""".format(better=MODE_AT_LEAST), [wmo,int(cycle),direction,lat,lon,
                    clean_float(read_scalar(nc,"JULD",index)),date_utc,
                    decode_str_var(nc,"POSITIONING_SYSTEM",index)[:16],mode,Path(nc_path).name[:256]])
                profile_id = cur.fetchone()[0]
                key = source_key(nc_path)
                rank = {"R": 0, "A": 1, "D": 2}.get(mode, 0)
                cur.execute("SELECT MAX(source_rank) FROM observations WHERE profile_id=%s AND source_file=%s AND source_profile=%s",
                            [profile_id,key,index])
                previous = cur.fetchone()[0]
                if previous is not None and previous > rank:
                    stats["skipped"] += 1
                    continue
                # File transaction ensures interrupted replacements roll back completely.
                cur.execute("DELETE FROM observations WHERE profile_id=%s AND source_file=%s AND source_profile=%s",
                            [profile_id,key,index])
                arrays = [read_parameter(nc, name, index) for name in PARAMETERS]
                pressure = arrays[0][0]
                rows = []
                for level in range(len(pressure) if pressure is not None else 0):
                    values = []
                    for array, qc in arrays:
                        flag = clean_qc(qc[level]) if qc is not None and level < len(qc) else None
                        value = clean_float(array[level]) if array is not None and level < len(array) and flag in QC_GOOD else None
                        values.extend([value, flag])
                    if values[0] is None:
                        continue
                    rows.append((profile_id,wmo,date_utc,lat,lon,level,*values,key,index,rank))
                if rows:
                    execute_values(cur, """INSERT INTO observations(profile_id,wmo_id,date_utc,latitude,longitude,depth_level,
                        pres,pres_qc,temp,temp_qc,psal,psal_qc,doxy,doxy_qc,chla,chla_qc,nitrate,nitrate_qc,bbp700,bbp700_qc,
                        source_file,source_profile,source_rank) VALUES %s""", rows, page_size=500)
                stats["profiles"] += 1
                stats["obs"] += len(rows)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        stats.update(profiles=0, obs=0, error=type(exc).__name__)
        log.error("Import failed for %s (%s)", Path(nc_path).name, type(exc).__name__)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file")
    group.add_argument("--dir")
    parser.add_argument("--init-schema", action="store_true", help="Apply non-destructive schema bootstrap/migration")
    parser.add_argument("--schema", default=str(Path(__file__).with_name("schema.sql")))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pattern", default="*.nc", help="File-name pattern searched under --dir, e.g. '*_Sprof.nc'")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    files = [Path(args.file)] if args.file else sorted(Path(args.dir).rglob(args.pattern))
    if args.limit:
        files = files[:args.limit]
    if not files:
        parser.error("No NetCDF files found")
    conn = get_connection()
    failures = 0
    try:
        if args.init_schema:
            apply_schema(conn, args.schema)
        for path in files:
            result = process_file(conn, path)
            failures += bool(result["error"])
            log.info("%s: %s", path.name, {k:v for k,v in result.items() if k != "file"})
        if failures < len(files):
            try:
                if refresh_summary(conn):
                    log.info("Refreshed profile_summary")
            except psycopg2.Error as exc:
                conn.rollback()
                failures += 1
                log.error("Could not refresh profile_summary (%s); run REFRESH MATERIALIZED VIEW "
                          "profile_summary in psql or re-run the import.", type(exc).__name__)
    finally:
        conn.close()
    return 1 if failures else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
