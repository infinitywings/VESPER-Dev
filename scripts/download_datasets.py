#!/usr/bin/env python3
"""Print authoritative dataset acquisition sources; no automatic license acceptance."""
import argparse
import json

CATALOG = {
    "HSSD": {"url": "https://huggingface.co/datasets/hssd/hssd-hab", "terms": "CC BY-NC 4.0 per upstream card; verify exact revision"},
    "HM3D": {"url": "https://aihabitat.org/datasets/hm3d/", "terms": "Academic noncommercial access agreement"},
    "ReplicaCAD": {"url": "https://aihabitat.org/datasets/replica_cad/", "terms": "CC BY 4.0 per upstream page"},
    "CASAS": {"url": "https://casas.wsu.edu/datasets/", "terms": "Review dataset-specific upstream terms"},
    "ARAS": {"url": "https://open.metu.edu.tr/handle/11511/36652", "terms": "Author contact/paper record; legacy download portal unavailable"},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", required=True, help="Print catalog; does not download data")
    parser.parse_args()
    print(json.dumps(CATALOG, indent=2))


if __name__ == "__main__":
    main()
