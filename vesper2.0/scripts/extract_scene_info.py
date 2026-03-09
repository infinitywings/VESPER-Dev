#!/usr/bin/env python3
"""
Extract scene information for VESPER - including navigable areas and room bounds.
"""

import os
import sys
import numpy as np

# Add habitat-lab to path
habitat_lab_path = os.path.expanduser("~/Desktop/vesper/habitat-lab-official")
if habitat_lab_path not in sys.path:
    sys.path.insert(0, habitat_lab_path)

import habitat
from omegaconf import OmegaConf


def extract_scene_info():
    """Extract navigable area information from the scene."""
    config = habitat.get_config(
        os.path.join(habitat_lab_path, "habitat-lab/habitat/config/benchmark/rearrange/play/play.yaml")
    )
    
    print("="*60)
    print("VESPER Scene Information Extractor")
    print("="*60)
    
    env = habitat.Env(config=config)
    env.reset()
    
    sim = env._sim
    pathfinder = sim.pathfinder
    
    # Get agent info
    agent = sim.articulated_agent
    agent_pos = agent.base_pos
    print(f"\nAgent Position: ({agent_pos[0]:.2f}, {agent_pos[1]:.2f}, {agent_pos[2]:.2f})")
    
    # Get navmesh bounds
    if pathfinder.is_loaded:
        bounds = pathfinder.get_bounds()
        print(f"\nNavmesh Bounds:")
        print(f"  Min: ({bounds[0][0]:.2f}, {bounds[0][1]:.2f}, {bounds[0][2]:.2f})")
        print(f"  Max: ({bounds[1][0]:.2f}, {bounds[1][1]:.2f}, {bounds[1][2]:.2f})")
        
        # Sample navigable points to understand the space
        print(f"\nSampling navigable points...")
        navigable_points = []
        for _ in range(100):
            point = pathfinder.get_random_navigable_point()
            if point is not None and not np.isnan(point).any():
                navigable_points.append(point)
        
        if navigable_points:
            points = np.array(navigable_points)
            print(f"  Sampled {len(points)} navigable points")
            print(f"  X range: {points[:, 0].min():.2f} to {points[:, 0].max():.2f}")
            print(f"  Y range: {points[:, 1].min():.2f} to {points[:, 1].max():.2f}")
            print(f"  Z range: {points[:, 2].min():.2f} to {points[:, 2].max():.2f}")
            
            # Find approximate room centers by clustering
            print(f"\nApproximate room centers (based on navigable clusters):")
            
            # Simple grid-based analysis
            x_bins = np.linspace(points[:, 0].min(), points[:, 0].max(), 5)
            z_bins = np.linspace(points[:, 2].min(), points[:, 2].max(), 5)
            
            centers = []
            for i in range(len(x_bins) - 1):
                for j in range(len(z_bins) - 1):
                    mask = (
                        (points[:, 0] >= x_bins[i]) & (points[:, 0] < x_bins[i+1]) &
                        (points[:, 2] >= z_bins[j]) & (points[:, 2] < z_bins[j+1])
                    )
                    if mask.sum() > 2:  # At least 3 points in this cell
                        center = points[mask].mean(axis=0)
                        centers.append(center)
                        print(f"  Area at ({center[0]:.2f}, {center[2]:.2f}) - {mask.sum()} points")
            
            # Print recommended room definitions
            print(f"\n" + "="*60)
            print("RECOMMENDED SCENE_ROOMS for this scene:")
            print("="*60)
            
            # Analyze the actual scene layout
            x_center = (points[:, 0].min() + points[:, 0].max()) / 2
            z_center = (points[:, 2].min() + points[:, 2].max()) / 2
            
            print(f'''"scene_v3_sc1": {{
    "living_room": {{"center": ({x_center:.1f}, 0.0, {z_center:.1f}), "size": (5.0, 5.0)}},
    "kitchen": {{"center": ({points[:, 0].max() - 2:.1f}, 0.0, {z_center:.1f}), "size": (3.0, 3.0)}},
    "bedroom": {{"center": ({points[:, 0].min() + 2:.1f}, 0.0, {z_center + 2:.1f}), "size": (4.0, 4.0)}},
    "bathroom": {{"center": ({points[:, 0].min() + 2:.1f}, 0.0, {z_center - 2:.1f}), "size": (2.5, 2.5)}},
    "hallway": {{"center": ({x_center:.1f}, 0.0, {points[:, 2].max() - 1:.1f}), "size": (2.0, 4.0)}},
}},''')
            
    else:
        print("\nNo navmesh loaded!")
    
    # Print scene config info
    print(f"\n" + "="*60)
    print("Scene Configuration:")
    print("="*60)
    print(f"Scene: {config.habitat.simulator.scene if hasattr(config.habitat.simulator, 'scene') else 'default'}")
    print(f"Dataset: {config.habitat.dataset.data_path}")
    
    env.close()
    print("\nDone!")


if __name__ == "__main__":
    extract_scene_info()
