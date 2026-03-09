#!/usr/bin/env python3
"""
VESPER Habitat 3D - Smart Home Simulation with Humanoid Agent
Uses proper HumanoidRearrangeController for humanoid movement
"""

import argparse
import os
import sys
import time
import numpy as np
import pygame
import magnum as mn

# Add habitat-lab to path
habitat_lab_path = os.path.expanduser("~/Desktop/vesper/habitat-lab-official")
if habitat_lab_path not in sys.path:
    sys.path.insert(0, habitat_lab_path)

# Habitat imports
import habitat
from habitat.config.default_structured_configs import (
    HumanoidJointActionConfig,
    ThirdRGBSensorConfig,
)
from habitat_sim.utils.settings import default_sim_settings
from habitat.articulated_agent_controllers import HumanoidRearrangeController
from omegaconf import OmegaConf

# VESPER imports
vesper_path = os.path.expanduser("~/Desktop/vesper")
if vesper_path not in sys.path:
    sys.path.insert(0, vesper_path)

from vesper.habitat.smart_home import SmartHomeIoT, SCENE_ROOMS
from vesper.habitat.task_manager import TaskManager
from vesper.habitat.hud import VesperHUD
from vesper.core.event_bus import EventBus


def make_config(scene_path: str):
    """Create Habitat configuration for humanoid with proper joint control"""
    config = habitat.get_config(
        os.path.join(habitat_lab_path, "habitat-lab/habitat/config/benchmark/rearrange/play/play.yaml")
    )
    
    with habitat.config.read_write(config):
        # Scene settings
        config.habitat.environment.iterator_options.shuffle = False
        config.habitat.simulator.habitat_sim_v0.allow_sliding = True
        
        # Configure humanoid agent
        agent_config = config.habitat.simulator.agents.main_agent
        agent_config.articulated_agent_type = "KinematicHumanoid"
        agent_config.articulated_agent_urdf = "data/humanoids/humanoid_data/female_2/female_2.urdf"
        agent_config.motion_data_path = "data/humanoids/humanoid_data/female_2/female_2_motion_data_smplx.pkl"
        
        # Enable third-person camera - 512x512 like interactive_play.py
        config.habitat.simulator.agents.main_agent.sim_sensors.update({
            "third_rgb_sensor": ThirdRGBSensorConfig(
                height=512,
                width=512,
            )
        })
        
        # Humanoid joint action (required for walking animation)
        config.habitat.task.actions["humanoidjoint_action"] = HumanoidJointActionConfig()
        
        # Use provided scene if exists
        if os.path.exists(scene_path):
            config.habitat.simulator.scene = scene_path
            
    return config


class VesperSimulation:
    """Main simulation class integrating all VESPER components"""
    
    def __init__(self, config, walk_pose_path: str):
        self.config = config
        self.walk_pose_path = walk_pose_path
        
        # Initialize Habitat environment
        print("[VESPER] Creating Habitat environment...")
        self.env = habitat.Env(config=config)
        
        # Reset and get initial observation
        print("[VESPER] Resetting environment...")
        self.observations = self.env.reset()
        
        # Initialize humanoid controller for walking
        print(f"[VESPER] Loading walk poses from: {walk_pose_path}")
        self.humanoid_controller = HumanoidRearrangeController(walk_pose_path)
        
        # Reset humanoid controller with current agent transformation matrix
        agent_transform = self.env._sim.articulated_agent.base_transformation
        self.humanoid_controller.reset(agent_transform)
        print(f"[VESPER] Humanoid initialized at position: {self.env._sim.articulated_agent.base_pos}")
        
        # Get agent for position tracking
        self.articulated_agent = self.env._sim.articulated_agent
        
        # Initialize VESPER components
        print("[VESPER] Initializing event bus...")
        self.event_bus = EventBus()
        
        print("[VESPER] Initializing smart home IoT...")
        self.smart_home = SmartHomeIoT(event_bus=self.event_bus)
        self.smart_home.setup_devices()
        
        print("[VESPER] Initializing task manager...")
        self.task_manager = TaskManager(event_bus=self.event_bus, smart_home=self.smart_home)
        
        # Pygame display - match sensor resolution like interactive_play.py
        self.display_size = (512, 512)
        pygame.init()
        pygame.display.set_caption("VESPER - Smart Home Simulation")
        self.screen = pygame.display.set_mode(self.display_size)
        self.clock = pygame.time.Clock()
        
        # Extract actual navigable regions from scene for better navigation
        self._analyze_scene_layout()
        
        # HUD - use screen size, not sensor size
        self.hud = VesperHUD(self.display_size[0], self.display_size[1])
        
        # Movement state
        self.base_vel = np.array([0.0, 0.0])  # Forward/backward, left/right
        self.move_speed = 0.8  # Slower for more accurate navigation
        self.turn_speed = 0.8
        
        # Task navigation state
        self.current_target = None
        self.navigating = False
        
        # Navigable points cache for better target selection
        self.navigable_points = []
        self._cache_navigable_points()
        
        print("[VESPER] Simulation ready!")
    
    def _analyze_scene_layout(self):
        """Analyze the scene to extract navigable room centers."""
        pathfinder = self.env._sim.pathfinder
        
        if not pathfinder.is_loaded:
            print("[VESPER] Warning: No navmesh loaded, navigation may clip through walls")
            return
        
        # Get navmesh bounds
        bounds = pathfinder.get_bounds()
        print(f"[VESPER] Scene bounds: ({bounds[0][0]:.1f}, {bounds[0][2]:.1f}) to ({bounds[1][0]:.1f}, {bounds[1][2]:.1f})")
        
        # Sample navigable points
        points = []
        for _ in range(200):
            point = pathfinder.get_random_navigable_point()
            if point is not None and not np.isnan(point).any():
                points.append(point)
        
        if points:
            points = np.array(points)
            x_center = (points[:, 0].min() + points[:, 0].max()) / 2
            z_center = (points[:, 2].min() + points[:, 2].max()) / 2
            
            # Create dynamic room definitions based on scene layout
            self.scene_rooms = {
                "living_room": {"center": (x_center, 0.0, z_center), "size": (5.0, 5.0)},
                "kitchen": {"center": (points[:, 0].max() - 1.5, 0.0, z_center), "size": (3.0, 3.0)},
                "bedroom": {"center": (points[:, 0].min() + 1.5, 0.0, z_center + 2), "size": (4.0, 4.0)},
                "bathroom": {"center": (points[:, 0].min() + 1.5, 0.0, z_center - 2), "size": (2.5, 2.5)},
                "hallway": {"center": (x_center, 0.0, points[:, 2].max() - 1), "size": (2.0, 4.0)},
            }
            
            print(f"[VESPER] Detected room layout:")
            for room, data in self.scene_rooms.items():
                print(f"  {room}: ({data['center'][0]:.1f}, {data['center'][2]:.1f})")
            
            # Update TaskManager with scene-specific rooms
            self.task_manager.scene_rooms = self.scene_rooms
    
    def _cache_navigable_points(self):
        """Cache navigable points for target validation."""
        pathfinder = self.env._sim.pathfinder
        if pathfinder.is_loaded:
            for _ in range(50):
                point = pathfinder.get_random_navigable_point()
                if point is not None and not np.isnan(point).any():
                    self.navigable_points.append(point)
        
    def get_agent_position(self):
        """Get current agent position as (x, y) tuple"""
        pos = self.articulated_agent.base_pos
        return (pos[0], pos[2])  # x, z in habitat = x, y in 2D
    
    def handle_input(self):
        """Handle keyboard input for manual control"""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return False
                # Manual movement keys
                elif event.key == pygame.K_i:  # Forward
                    self.base_vel[0] = self.move_speed
                elif event.key == pygame.K_k:  # Backward
                    self.base_vel[0] = -self.move_speed
                elif event.key == pygame.K_j:  # Turn left
                    self.base_vel[1] = self.turn_speed
                elif event.key == pygame.K_l:  # Turn right
                    self.base_vel[1] = -self.turn_speed
                # Quick teleport to rooms (for testing)
                elif event.key == pygame.K_1:
                    self._teleport_to_room("kitchen")
                elif event.key == pygame.K_2:
                    self._teleport_to_room("bedroom")
                elif event.key == pygame.K_3:
                    self._teleport_to_room("bathroom")
                elif event.key == pygame.K_4:
                    self._teleport_to_room("hallway")
                    
            elif event.type == pygame.KEYUP:
                if event.key in (pygame.K_i, pygame.K_k):
                    self.base_vel[0] = 0.0
                elif event.key in (pygame.K_j, pygame.K_l):
                    self.base_vel[1] = 0.0
                    self._manual_input_active = False
                    
        return True
    
    def _has_manual_input(self) -> bool:
        """Check if user is providing manual keyboard input."""
        keys = pygame.key.get_pressed()
        return keys[pygame.K_i] or keys[pygame.K_k] or keys[pygame.K_j] or keys[pygame.K_l]
    
    def _teleport_to_room(self, room_name: str):
        """Teleport agent to a room (for testing)"""
        if room_name in SCENE_ROOMS:
            coords = SCENE_ROOMS[room_name]
            new_pos = mn.Vector3(coords[0], 0.0, coords[1])
            self.articulated_agent.base_pos = new_pos
            self.humanoid_controller.reset(new_pos)
            print(f"[VESPER] Teleported to {room_name}: {coords}")
    
    def update_navigation(self):
        """Navigation is now handled by TaskManager.update()."""
        # Get status for HUD updates
        status = self.task_manager.get_status()
        self.navigating = status["execution_state"] == "moving"
        self.current_target = status["current_target"]
    
    def _check_navmesh_collision(self, target_pos: mn.Vector3) -> mn.Vector3:
        """
        Check if target position is navigable and snap to navmesh if needed.
        Returns the valid position (snapped to navmesh or original if valid).
        """
        pathfinder = self.env._sim.pathfinder
        
        if not pathfinder.is_loaded:
            # No navmesh, allow movement (but might clip through walls)
            return target_pos
        
        # Check if target is navigable
        if pathfinder.is_navigable(target_pos):
            return target_pos
        
        # Try to snap to nearest navigable point
        snapped = pathfinder.snap_point(target_pos)
        if snapped is not None and not np.isnan(snapped).any():
            return mn.Vector3(snapped)
        
        # Can't navigate there, return current position
        return self.articulated_agent.base_pos
    
    def step_simulation(self):
        """Step the simulation with current velocity"""
        # Check if we have any movement input
        has_movement = np.linalg.norm(self.base_vel) > 0.01
        relative_pos = mn.Vector3(0, 0, 0)
        new_position = None
        
        if has_movement:
            # Get current position
            current_pos = self.articulated_agent.base_pos
            
            # Calculate movement delta (scaled by time step)
            move_scale = 0.1  # Movement per frame (increased for visibility)
            
            # Get agent's forward direction from transformation
            agent_transform = self.articulated_agent.base_transformation
            forward = agent_transform.transform_vector(mn.Vector3(0, 0, -1))
            right = agent_transform.transform_vector(mn.Vector3(1, 0, 0))
            
            # Calculate target position
            # base_vel[0] is forward/backward, base_vel[1] is turn (which we use for lateral)
            delta = forward * self.base_vel[0] * move_scale + right * self.base_vel[1] * move_scale
            target_pos = current_pos + delta
            
            # Check navmesh collision
            pathfinder = self.env._sim.pathfinder
            if pathfinder.is_loaded:
                # try_step_no_sliding returns valid position respecting navmesh
                valid_pos = pathfinder.try_step_no_sliding(current_pos, target_pos)
                
                # Check if we actually moved (not blocked by wall)
                if np.allclose(valid_pos, current_pos, atol=0.001):
                    # Blocked - don't animate walking
                    has_movement = False
                else:
                    # We can move! Calculate relative movement for animation
                    actual_delta = mn.Vector3(valid_pos) - current_pos
                    relative_pos = mn.Vector3(
                        actual_delta[0],
                        0.0,
                        actual_delta[2]
                    )
                    # Store the validated new position
                    new_position = mn.Vector3(valid_pos)
            else:
                # No navmesh - use original movement directly
                relative_pos = delta
                new_position = target_pos
        
        if has_movement and new_position is not None:
            # Calculate walking pose animation
            self.humanoid_controller.calculate_walk_pose(relative_pos)
            humanoid_action = self.humanoid_controller.get_pose()
            
            # IMPORTANT: Actually move the humanoid to the new position!
            self.articulated_agent.base_pos = new_position
            
            action = {
                "action": "humanoidjoint_action",
                "action_args": {
                    "human_joints_trans": humanoid_action
                }
            }
        else:
            # No movement - idle pose
            self.humanoid_controller.calculate_walk_pose(mn.Vector3(0, 0, 0))
            humanoid_action = self.humanoid_controller.get_pose()
            
            action = {
                "action": "humanoidjoint_action",
                "action_args": {
                    "human_joints_trans": humanoid_action
                }
            }
        
        # Step the environment
        self.observations = self.env.step(action)
        
    def render(self):
        """Render the simulation to pygame display"""
        # Get observation image
        if "third_rgb_sensor" in self.observations:
            frame = self.observations["third_rgb_sensor"]
        elif "robot_third_rgb" in self.observations:
            frame = self.observations["robot_third_rgb"]
        elif "head_rgb" in self.observations:
            frame = self.observations["head_rgb"]
        else:
            # Use first available sensor
            for key in self.observations:
                if "rgb" in key.lower() and isinstance(self.observations[key], np.ndarray):
                    frame = self.observations[key]
                    break
            else:
                frame = np.zeros((512, 512, 3), dtype=np.uint8)
        
        # Convert to pygame surface
        frame = np.ascontiguousarray(frame[:, :, :3])  # Ensure RGB only
        frame_surface = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
        
        # Only scale if frame size doesn't match display (should be rare now)
        if frame_surface.get_size() != self.display_size:
            frame_surface = pygame.transform.smoothscale(frame_surface, self.display_size)
        
        # Draw frame
        self.screen.blit(frame_surface, (0, 0))
        
        # Get agent position for HUD
        agent_pos = self.articulated_agent.base_pos
        
        # Render HUD overlay with proper task status
        task_status = self.task_manager.get_status()
        self.hud.render(
            self.screen,
            device_states=self.smart_home.get_device_states(),
            event_log=self.smart_home.event_log[-5:] if hasattr(self.smart_home, 'event_log') else [],
            agent_pos=np.array([agent_pos[0], agent_pos[1], agent_pos[2]]),
            fps=self.clock.get_fps(),
            task_status=task_status,
            mqtt_connected=self.smart_home.mqtt_bridge is not None if hasattr(self.smart_home, 'mqtt_bridge') else False,
            is_humanoid=True,
        )
        
        # Update display
        pygame.display.flip()
        
    def run(self, never_end: bool = False):
        """Main simulation loop"""
        print("\n" + "="*60)
        print("VESPER Smart Home Simulation")
        print("="*60)
        print("Controls:")
        print("  I/K - Move forward/backward")
        print("  J/L - Turn left/right")
        print("  1/2/3/4 - Teleport to kitchen/bedroom/bathroom/hallway")
        print("  ESC - Exit")
        print("="*60 + "\n")
        
        running = True
        frame_count = 0
        sim_time = 0.0
        
        while running:
            # Handle input
            running = self.handle_input()
            
            # Update task manager (handles task generation + navigation)
            agent_pos = self.articulated_agent.base_pos
            new_task, movement = self.task_manager.update(sim_time, np.array([agent_pos[0], agent_pos[1], agent_pos[2]]))
            
            # Apply movement from task manager (always update, including stopping)
            # Only apply autonomous movement if no manual input
            if not self._has_manual_input():
                self.base_vel[0] = movement[0]
                self.base_vel[1] = movement[1]
            
            # Debug: print base_vel periodically
            if frame_count % 60 == 0:
                print(f"[Main] base_vel: ({self.base_vel[0]:.2f}, {self.base_vel[1]:.2f}), manual_input: {self._has_manual_input()}")
            
            sim_time += 1.0 / 60.0  # Increment simulation time
            
            # Step simulation
            self.step_simulation()
            
            # Render
            self.render()
            
            # Frame rate control
            self.clock.tick(60)
            frame_count += 1
            
            # Check if should end
            if not never_end and self.env.episode_over:
                print("[VESPER] Episode ended")
                break
                
        self.cleanup()
        
    def cleanup(self):
        """Clean up resources"""
        print("[VESPER] Shutting down...")
        self.env.close()
        pygame.quit()


def main():
    parser = argparse.ArgumentParser(description="VESPER Habitat 3D Simulation")
    parser.add_argument("--never-end", action="store_true", help="Keep running indefinitely")
    parser.add_argument(
        "--walk-pose-path",
        default=os.path.join(habitat_lab_path, "data/humanoids/humanoid_data/walking_motion_processed_smplx.pkl"),
        help="Path to walking motion data"
    )
    parser.add_argument(
        "--scene",
        default=os.path.join(habitat_lab_path, "data/hssd-hab/scenes-uncluttered/102344280.scene_instance.json"),
        help="Path to scene file"
    )
    args = parser.parse_args()
    
    # Verify walk pose file exists
    if not os.path.exists(args.walk_pose_path):
        print(f"[ERROR] Walk pose file not found: {args.walk_pose_path}")
        print("Please ensure the humanoid data is downloaded.")
        sys.exit(1)
    
    # Create config
    config = make_config(args.scene)
    
    # Create and run simulation
    sim = VesperSimulation(config, args.walk_pose_path)
    sim.run(never_end=args.never_end)


if __name__ == "__main__":
    main()
