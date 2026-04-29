"""
FloatChat — ARGO Data Downloader
=================================
Downloads sample ARGO float profiles from the IFREMER GDAC FTP.
Focuses on Indian Ocean floats for the PoC.

Usage:
    python download_argo.py --ocean indian --n-floats 5 --out-dir ./data/argo/

Or download specific WMO IDs:
    python download_argo.py --wmo 2902115 2902116 --out-dir ./data/argo/
"""

import os
import ftplib
import argparse
import logging
from pathlib import Path

log = logging.getLogger("argo_downloader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

# IFREMER GDAC — primary mirror
GDAC_FTP  = "ftp.ifremer.fr"
GDAC_ROOT = "/ifremer/argo"

# DAC folders that cover the Indian Ocean
INDIAN_OCEAN_DACS = ["incois", "bodc", "coriolis", "csio", "kordi"]

# Curated WMO IDs: well-sampled Indian Ocean BGC floats (INCOIS / BODC)
SAMPLE_WMO = [
    "2902115",   # INCOIS, Arabian Sea
    "2902116",   # INCOIS, Arabian Sea
    "2902264",   # INCOIS, Bay of Bengal
    "6901486",   # BODC,   Indian Ocean
    "6901487",   # BODC,   Indian Ocean
    "1901499",   # CORIOLIS, Indian Ocean
    "1901500",   # CORIOLIS, Indian Ocean
]


def list_ftp_dir(ftp: ftplib.FTP, path: str) -> list:
    try:
        return ftp.nlst(path)
    except ftplib.error_perm:
        return []


def download_file(ftp: ftplib.FTP, remote_path: str, local_path: Path) -> bool:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if local_path.exists():
        log.info("  Already exists, skipping: %s", local_path.name)
        return True
    try:
        with open(local_path, "wb") as f:
            ftp.retrbinary(f"RETR {remote_path}", f.write)
        log.info("  Downloaded: %s  (%.1f KB)", local_path.name, local_path.stat().st_size / 1024)
        return True
    except ftplib.error_perm as e:
        log.warning("  FTP error for %s: %s", remote_path, e)
        return False


def download_by_wmo(wmo_ids: list, out_dir: Path, profile_type: str = "profiles"):
    """
    Download all .nc profile files for a list of WMO IDs.
    profile_type: 'profiles' (one file per cycle) or 'Sprof' (single merged BGC file).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Connecting to GDAC FTP: %s", GDAC_FTP)

    try:
        ftp = ftplib.FTP(GDAC_FTP, timeout=30)
        ftp.login()
        log.info("Connected.")
    except Exception as e:
        log.error("Cannot connect to GDAC FTP: %s", e)
        log.info("TIP: Download manually from https://data-argo.ifremer.fr/dac/")
        return

    downloaded = 0
    for wmo in wmo_ids:
        log.info("Looking up WMO %s ...", wmo)

        # Search across all DAC directories
        found = False
        for dac in INDIAN_OCEAN_DACS + ["aoml", "meds", "jma", "nmdis"]:
            dac_wmo_path = f"{GDAC_ROOT}/dac/{dac}/{wmo}"
            entries = list_ftp_dir(ftp, dac_wmo_path)
            if not entries:
                continue

            found = True
            log.info("  Found in DAC: %s", dac)
            wmo_out = out_dir / dac / wmo
            wmo_out.mkdir(parents=True, exist_ok=True)

            if profile_type == "Sprof":
                # Single merged BGC file: <WMO>_Sprof.nc
                remote = f"{dac_wmo_path}/{wmo}_Sprof.nc"
                download_file(ftp, remote, wmo_out / f"{wmo}_Sprof.nc")
                downloaded += 1
            else:
                # Individual profile files in /profiles/ subdirectory
                prof_dir = f"{dac_wmo_path}/profiles"
                nc_files = list_ftp_dir(ftp, prof_dir)
                for nc_file in nc_files:
                    if not nc_file.endswith(".nc"):
                        continue
                    fname = Path(nc_file).name
                    # Prefer R (real-time) files; skip D (delayed) duplicates unless no R exists
                    if fname.startswith("D") and any(
                        Path(f).name == "R" + fname[1:] for f in nc_files
                    ):
                        continue
                    ok = download_file(ftp, nc_file, wmo_out / "profiles" / fname)
                    if ok:
                        downloaded += 1
            break

        if not found:
            log.warning("  WMO %s not found in any DAC. Try: https://data-argo.ifremer.fr", wmo)

    ftp.quit()
    log.info("Done. %d file(s) downloaded to %s", downloaded, out_dir)


def main():
    parser = argparse.ArgumentParser(description="Download ARGO .nc files from IFREMER GDAC")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--wmo", nargs="+", help="Specific WMO float IDs to download")
    group.add_argument("--sample", action="store_true", help="Download the curated Indian Ocean sample set")
    parser.add_argument("--out-dir", default="./data/argo", help="Output directory")
    parser.add_argument(
        "--type",
        choices=["profiles", "Sprof"],
        default="profiles",
        help="'profiles' = one .nc per cycle (default); 'Sprof' = merged BGC file",
    )
    args = parser.parse_args()

    wmo_ids = args.wmo if args.wmo else SAMPLE_WMO
    download_by_wmo(wmo_ids, Path(args.out_dir), profile_type=args.type)


if __name__ == "__main__":
    main()
