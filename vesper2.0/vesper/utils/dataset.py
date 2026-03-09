"""
Dataset utilities for downloading and managing Habitat datasets.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


# Available datasets with their Habitat UIDs
AVAILABLE_DATASETS = {
    "hssd-hab": {
        "uid": "hssd-hab",
        "description": "Habitat Synthetic Scenes Dataset (compressed for Habitat)",
        "size": "~8GB",
    },
    "replica-cad": {
        "uid": "replica_cad_dataset",
        "description": "ReplicaCAD high-fidelity indoor scenes",
        "size": "~3GB",
    },
    "hab3-bench-assets": {
        "uid": "hab3_bench_assets",
        "description": "Habitat 3.0 benchmark assets (subset of HSSD)",
        "size": "~1GB",
    },
    "mp3d-example": {
        "uid": "mp3d_example_scene",
        "description": "Example Matterport3D scene for testing",
        "size": "~200MB",
    },
}


def list_available_datasets() -> dict[str, dict]:
    """
    List available datasets for download.
    
    Returns:
        Dictionary of dataset names to their metadata.
    """
    return AVAILABLE_DATASETS.copy()


def download_dataset(
    dataset_name: str,
    output_dir: Optional[str | Path] = None,
    verbose: bool = True,
) -> bool:
    """
    Download a Habitat dataset.
    
    Uses habitat_sim.utils.datasets_download under the hood.
    
    Args:
        dataset_name: Name of the dataset (e.g., "hssd-hab")
        output_dir: Optional custom output directory
        verbose: Print download progress
        
    Returns:
        True if download was successful
        
    Raises:
        ValueError: If dataset name is not recognized
    """
    if dataset_name not in AVAILABLE_DATASETS:
        available = ", ".join(AVAILABLE_DATASETS.keys())
        raise ValueError(
            f"Unknown dataset: {dataset_name}. Available: {available}"
        )
    
    dataset_info = AVAILABLE_DATASETS[dataset_name]
    uid = dataset_info["uid"]
    
    logger.info(f"Downloading dataset: {dataset_name} ({dataset_info['size']})")
    
    # Build command
    cmd = [
        sys.executable,
        "-m",
        "habitat_sim.utils.datasets_download",
        "--uids",
        uid,
    ]
    
    if output_dir:
        cmd.extend(["--data-path", str(output_dir)])
    
    if not verbose:
        cmd.append("--no-replace")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True,
            check=True,
        )
        logger.info(f"Dataset {dataset_name} downloaded successfully")
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to download dataset: {e}")
        if e.stderr:
            logger.error(f"Error output: {e.stderr}")
        return False
        
    except FileNotFoundError:
        logger.error(
            "habitat_sim not installed. Install with: "
            "conda install habitat-sim -c conda-forge -c aihabitat"
        )
        return False


def verify_dataset(dataset_name: str, data_path: Optional[Path] = None) -> bool:
    """
    Verify that a dataset is properly installed.
    
    Args:
        dataset_name: Name of the dataset
        data_path: Path to check. If None, uses default Habitat data path.
        
    Returns:
        True if dataset appears to be installed correctly
    """
    # Try to import habitat_sim to get default data path
    try:
        import habitat_sim
        if data_path is None:
            # Default Habitat data path
            data_path = Path(habitat_sim.utils.datasets_download.default_data_path())
    except ImportError:
        if data_path is None:
            data_path = Path.home() / "data"
    
    # Check for dataset-specific markers
    markers = {
        "hssd-hab": ["hssd-hab"],
        "replica-cad": ["replica_cad"],
        "hab3-bench-assets": ["hab3_bench_assets"],
        "mp3d-example": ["scene_datasets/mp3d_example"],
    }
    
    if dataset_name not in markers:
        logger.warning(f"No verification markers for dataset: {dataset_name}")
        return False
    
    for marker in markers[dataset_name]:
        marker_path = data_path / marker
        if marker_path.exists():
            logger.info(f"Dataset {dataset_name} found at: {marker_path}")
            return True
    
    logger.warning(f"Dataset {dataset_name} not found in {data_path}")
    return False


def get_scene_list(dataset_name: str, data_path: Optional[Path] = None) -> list[str]:
    """
    Get list of available scenes in a dataset.
    
    Args:
        dataset_name: Name of the dataset
        data_path: Path to dataset. If None, uses default.
        
    Returns:
        List of scene identifiers
    """
    # This is a placeholder - actual implementation would
    # scan the dataset directory for available scenes
    logger.warning("get_scene_list not fully implemented")
    return []
