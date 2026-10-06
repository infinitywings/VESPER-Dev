#!/usr/bin/env python3
"""
Download and prepare reference activity datasets for VESPER evaluation.

Supported datasets:
  - CASAS  (Washington State University Smart Home)
  - ARAS   (Bogazici University Activities of Daily Living)

Usage:
    python -m vesper.evaluation.download_datasets --dataset casas --output data/datasets/casas
    python -m vesper.evaluation.download_datasets --dataset aras --output data/datasets/aras
    python -m vesper.evaluation.download_datasets --dataset all
"""

from __future__ import annotations

import gzip
import io
import logging
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# =============================================================================
# CASAS Smart Home Dataset
# =============================================================================

CASAS_URLS = {
    # Aruba single-resident home (Nov 2010 – Jun 2011)
    "aruba": "https://casas.wsu.edu/datasets/aruba.zip",
    # Milan two-resident home
    "milan": "https://casas.wsu.edu/datasets/milan.zip",
    # Cairo apartment
    "cairo": "https://casas.wsu.edu/datasets/cairo.zip",
}


def download_casas(output_dir: str = "data/datasets/casas", homes: list = None):
    """
    Download CASAS annotated activity data.

    Each dataset is a single flat file with lines like:
        2010-11-04 00:03:50.209589  M003  ON  Sleeping

    The file will be saved as {output_dir}/{home}/data.txt.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    homes = homes or list(CASAS_URLS.keys())

    for home in homes:
        url = CASAS_URLS.get(home)
        if not url:
            logger.warning(f"Unknown CASAS home: {home}")
            continue

        dest = out / home
        if dest.exists() and any(dest.iterdir()):
            logger.info(f"CASAS/{home} already downloaded → {dest}")
            continue

        dest.mkdir(parents=True, exist_ok=True)
        logger.info(f"Downloading CASAS/{home} from {url} ...")

        try:
            resp = urllib.request.urlopen(url, timeout=120)
            data = resp.read()

            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(str(dest))

            logger.info(f"  ✓ Extracted to {dest}")
        except Exception as e:
            logger.error(f"  ✗ Failed to download {home}: {e}")
            # Create a README noting the failure
            (dest / "DOWNLOAD_FAILED.txt").write_text(
                f"Failed to download from {url}\n"
                f"Error: {e}\n\n"
                f"Manual download:\n"
                f"  1. Visit https://casas.wsu.edu/datasets/\n"
                f"  2. Download the {home} dataset\n"
                f"  3. Extract contents to {dest}/\n"
            )

    # Write metadata
    meta = out / "README.md"
    if not meta.exists():
        meta.write_text(
            "# CASAS Smart Home Datasets\n\n"
            "Source: https://casas.wsu.edu/datasets/\n\n"
            "Reference:\n"
            "  D. Cook et al., \"CASAS: A Smart Home in a Box,\" IEEE Computer, 2013.\n\n"
            "Datasets:\n"
            "  - aruba: Single resident, 7 months\n"
            "  - milan: Two residents + pet, 3 months\n"
            "  - cairo: Single resident, 2 months\n"
        )


# =============================================================================
# ARAS Activity Recognition Dataset
# =============================================================================

ARAS_URL = "https://www.cmpe.boun.edu.tr/aras/ARAS.zip"


def download_aras(output_dir: str = "data/datasets/aras"):
    """
    Download ARAS activity recognition dataset.

    ARAS contains data from 2 real houses, each with 20 force-sensitive
    resistors and 27 activity labels, recorded over 30 days.
    """
    out = Path(output_dir)
    if out.exists() and any(out.iterdir()):
        logger.info(f"ARAS already downloaded → {out}")
        return

    out.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading ARAS from {ARAS_URL} ...")

    try:
        resp = urllib.request.urlopen(ARAS_URL, timeout=180)
        data = resp.read()

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(str(out))

        logger.info(f"  ✓ Extracted to {out}")
    except Exception as e:
        logger.error(f"  ✗ Failed to download ARAS: {e}")
        (out / "DOWNLOAD_FAILED.txt").write_text(
            f"Failed to download from {ARAS_URL}\n"
            f"Error: {e}\n\n"
            f"Manual download:\n"
            f"  1. Visit https://www.cmpe.boun.edu.tr/aras/\n"
            f"  2. Download ARAS.zip\n"
            f"  3. Extract contents to {out}/\n"
        )

    meta = out / "README.md"
    if not meta.exists():
        meta.write_text(
            "# ARAS Activity Recognition Dataset\n\n"
            "Source: https://www.cmpe.boun.edu.tr/aras/\n\n"
            "Reference:\n"
            "  H. Alemdar et al., \"ARAS Human Activity Datasets in\n"
            "  Multiple Homes with Multiple Residents,\" PervasiveHealth, 2013.\n\n"
            "Contains:\n"
            "  - House A: 2 residents, 27 activities, 30 days\n"
            "  - House B: 2 residents, 27 activities, 30 days\n"
            "  - 20 binary sensors per house\n"
        )


# =============================================================================
# CLI
# =============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Download reference activity datasets for VESPER evaluation"
    )
    parser.add_argument(
        "--dataset", "-d",
        choices=["casas", "aras", "all"],
        default="all",
        help="Which dataset to download",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output directory (defaults per dataset)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if args.dataset in ("casas", "all"):
        download_casas(args.output or "data/datasets/casas")

    if args.dataset in ("aras", "all"):
        download_aras(args.output or "data/datasets/aras")

    print("Done.")


if __name__ == "__main__":
    main()
