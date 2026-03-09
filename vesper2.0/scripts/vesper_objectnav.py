#!/usr/bin/env python3
"""
VESPER ObjectNav Demo - First-person navigation in photorealistic indoor scenes.
Demonstrates "Go to object/room" style navigation like the Habitat AI demo.
"""

import os
import sys

# Set up paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "habitat-lab-official"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "habitat-lab-official", "habitat-lab"))

import numpy as np
import magnum as mn
import habitat_sim
from habitat_sim.utils.common import quat_from_angle_axis, quat_to_angle_axis
from habitat_sim.errors import GreedyFollowerError
import pygame
import random
from typing import Optional, Dict, List, Tuple
import json

# Import VESPER LLM client
sys.path.insert(0, os.path.join(PROJECT_ROOT, "vesper"))
from vesper.agents.llm_client import LLMClient, LLMConfig, LLMMessage

# Import VESPER modular components
from vesper.habitat.iot_overlay import IoTDeviceManager, IoTOverlayRenderer
from vesper.habitat.humanoid import HumanoidController, HumanoidRenderer
from vesper.habitat.vesper_integration import VesperIntegration, VesperConfig

# High-quality rendering config
RESOLUTION = (1280, 720)  # HD resolution
SENSOR_HEIGHT = 1.5  # Human eye height
MOVE_SPEED = 0.15  # m per step
TURN_SPEED = 5.0  # degrees per step

# Third-person camera settings
THIRD_PERSON_DISTANCE = 3.0
THIRD_PERSON_HEIGHT = 2.0


class ObjectNavDemo:
    """First-person navigation demo with object/room goals."""
    
    def __init__(self):
        self.sim: Optional[habitat_sim.Simulator] = None
        self.agent = None
        self.path_follower = None  # GreedyGeodesicFollower
        self.current_goal = None
        self.current_task = None  # LLM-generated task description
        self.objects_in_scene: Dict[str, List[mn.Vector3]] = {}
        self.humanoid = None  # Articulated object for humanoid
        self.use_third_person = False  # Start in first-person view
        
        # LLM client for task generation
        self.llm_client = None
        self.use_llm = False  # Enable LLM task generation
        
        # VESPER integration (modular components)
        self.vesper: Optional[VesperIntegration] = None
        
        # Navigation target types (rooms/objects)
        self.target_types = [
            "toilet", "bed", "couch", "chair", "dining table",
            "refrigerator", "tv", "sink", "bathtub", "kitchen"
        ]
        
    def find_scene(self) -> Tuple[str, Optional[str]]:
        """Find a scene file to load. Returns (scene_path, config_path)."""
        data_path = os.path.join(PROJECT_ROOT, "data")
        
        # Try HSSD-hab scenes first (high quality with semantics support)
        hssd_path = os.path.join(data_path, "scene_datasets", "hssd-hab", "scenes")
        if os.path.exists(hssd_path):
            scenes = [f for f in os.listdir(hssd_path) if f.endswith(".scene_instance.json")]
            if scenes:
                scene = random.choice(scenes[:10])
                config_path = os.path.join(os.path.dirname(hssd_path), "hssd-hab.scene_dataset_config.json")
                return os.path.join(hssd_path, scene), config_path
        
        # Try HM3D scenes with semantic annotations
        hm3d_path = os.path.join(data_path, "scene_datasets", "hm3d", "example")
        if os.path.exists(hm3d_path):
            config_path = os.path.join(hm3d_path, "hm3d_annotated_example_basis.scene_dataset_config.json")
            if not os.path.exists(config_path):
                config_path = os.path.join(hm3d_path, "hm3d_annotated_basis.scene_dataset_config.json")
            
            for subdir in os.listdir(hm3d_path):
                scene_dir = os.path.join(hm3d_path, subdir)
                if os.path.isdir(scene_dir):
                    for f in os.listdir(scene_dir):
                        if f.endswith(".basis.glb"):
                            scene_file = os.path.join(scene_dir, f)
                            return scene_file, config_path if os.path.exists(config_path) else None
        
        # Try ReplicaCAD
        replica_path = os.path.join(data_path, "replica_cad", "configs", "scenes")
        if os.path.exists(replica_path):
            scenes = [f for f in os.listdir(replica_path) if f.endswith(".scene_instance.json")]
            if scenes:
                config = os.path.join(data_path, "replica_cad", "replicaCAD.scene_dataset_config.json")
                return os.path.join(replica_path, random.choice(scenes)), config
                
        raise FileNotFoundError("No scene files found! Please download a dataset.")
    
    def create_simulator(self, scene_path: str, config_path: Optional[str] = None) -> habitat_sim.Simulator:
        """Create simulator with high-quality rendering settings."""
        
        # Backend configuration
        backend_cfg = habitat_sim.SimulatorConfiguration()
        backend_cfg.scene_id = scene_path
        backend_cfg.enable_physics = True
        backend_cfg.physics_config_file = os.path.join(
            PROJECT_ROOT, "data", "default.physics_config.json"
        )
        
        # Use provided config or search for one
        if config_path and os.path.exists(config_path):
            backend_cfg.scene_dataset_config_file = config_path
            print(f"Using scene dataset config: {os.path.basename(config_path)}")
        
        # Agent configuration with high-res sensors
        agent_cfg = habitat_sim.agent.AgentConfiguration()
        agent_cfg.height = SENSOR_HEIGHT
        agent_cfg.radius = 0.1
        
        # RGB sensor (first-person view)
        rgb_sensor = habitat_sim.CameraSensorSpec()
        rgb_sensor.uuid = "rgb"
        rgb_sensor.sensor_type = habitat_sim.SensorType.COLOR
        rgb_sensor.resolution = [RESOLUTION[1], RESOLUTION[0]]
        rgb_sensor.position = [0.0, SENSOR_HEIGHT, 0.0]
        rgb_sensor.hfov = 90  # Wide FOV for better spatial awareness
        
        # Depth sensor for obstacle awareness
        depth_sensor = habitat_sim.CameraSensorSpec()
        depth_sensor.uuid = "depth"
        depth_sensor.sensor_type = habitat_sim.SensorType.DEPTH
        depth_sensor.resolution = [RESOLUTION[1], RESOLUTION[0]]
        depth_sensor.position = [0.0, SENSOR_HEIGHT, 0.0]
        depth_sensor.hfov = 90
        
        # Semantic sensor for object detection
        semantic_sensor = habitat_sim.CameraSensorSpec()
        semantic_sensor.uuid = "semantic"
        semantic_sensor.sensor_type = habitat_sim.SensorType.SEMANTIC
        semantic_sensor.resolution = [RESOLUTION[1] // 4, RESOLUTION[0] // 4]  # Lower res
        semantic_sensor.position = [0.0, SENSOR_HEIGHT, 0.0]
        semantic_sensor.hfov = 90
        
        # Third-person camera (looking at agent from behind and above)
        third_person_sensor = habitat_sim.CameraSensorSpec()
        third_person_sensor.uuid = "third_rgb"
        third_person_sensor.sensor_type = habitat_sim.SensorType.COLOR
        third_person_sensor.resolution = [RESOLUTION[1], RESOLUTION[0]]
        third_person_sensor.position = [0.0, THIRD_PERSON_HEIGHT, THIRD_PERSON_DISTANCE]
        third_person_sensor.orientation = [-0.3, 0.0, 0.0]  # Look down slightly
        third_person_sensor.hfov = 90
        
        agent_cfg.sensor_specifications = [rgb_sensor, depth_sensor, semantic_sensor, third_person_sensor]
        
        # Action space
        agent_cfg.action_space = {
            "move_forward": habitat_sim.agent.ActionSpec(
                "move_forward", habitat_sim.agent.ActuationSpec(amount=MOVE_SPEED)
            ),
            "move_backward": habitat_sim.agent.ActionSpec(
                "move_backward", habitat_sim.agent.ActuationSpec(amount=MOVE_SPEED)
            ),
            "turn_left": habitat_sim.agent.ActionSpec(
                "turn_left", habitat_sim.agent.ActuationSpec(amount=TURN_SPEED)
            ),
            "turn_right": habitat_sim.agent.ActionSpec(
                "turn_right", habitat_sim.agent.ActuationSpec(amount=TURN_SPEED)
            ),
            "look_up": habitat_sim.agent.ActionSpec(
                "look_up", habitat_sim.agent.ActuationSpec(amount=TURN_SPEED)
            ),
            "look_down": habitat_sim.agent.ActionSpec(
                "look_down", habitat_sim.agent.ActuationSpec(amount=TURN_SPEED)
            ),
        }
        
        cfg = habitat_sim.Configuration(backend_cfg, [agent_cfg])
        
        sim = habitat_sim.Simulator(cfg)
        
        # Initialize navmesh for pathfinding
        if not sim.pathfinder.is_loaded:
            print("Computing navmesh...")
            navmesh_settings = habitat_sim.NavMeshSettings()
            navmesh_settings.set_defaults()
            navmesh_settings.agent_height = SENSOR_HEIGHT
            navmesh_settings.agent_radius = 0.1
            sim.recompute_navmesh(sim.pathfinder, navmesh_settings)
        
        return sim
    
    def init_path_follower(self):
        """Initialize the GreedyGeodesicFollower for proper pathfinding navigation."""
        # This is what Habitat actually uses for ShortestPathFollower
        self.path_follower = self.sim.make_greedy_follower(
            agent_id=0,
            goal_radius=0.5,  # Stop when within 0.5m of goal
            stop_key="stop",
            forward_key="move_forward",
            left_key="turn_left",
            right_key="turn_right",
        )
        print("Initialized GreedyGeodesicFollower for navigation")
    
    def get_random_navigable_point(self) -> mn.Vector3:
        """Get a random navigable point in the scene."""
        return self.sim.pathfinder.get_random_navigable_point()
    
    def load_humanoid(self):
        """Load a humanoid model at the agent's position."""
        # Disabled for now - URDF loading creates T-pose without animation
        # Would need proper HumanoidRearrangeController for animated humanoid
        print("Humanoid disabled - using first-person navigation")
        return None
    
    def update_humanoid_position(self):
        """Update humanoid position to match agent."""
        if self.humanoid is not None:
            agent_state = self.agent.get_state()
            self.humanoid.translation = agent_state.position
            # Note: Rotation update requires magnum Quaternion, skipping for now
    
    def find_path_to_goal(self, goal_pos: mn.Vector3) -> Optional[habitat_sim.ShortestPath]:
        """Find shortest path to goal."""
        agent_state = self.agent.get_state()
        start_pos = agent_state.position
        
        path = habitat_sim.ShortestPath()
        path.requested_start = start_pos
        path.requested_end = goal_pos
        
        if self.sim.pathfinder.find_path(path):
            return path
        return None
    
    def get_action_to_goal(self, goal_pos: mn.Vector3) -> Optional[str]:
        """Get the next action to move toward goal using GreedyGeodesicFollower.
        
        This is the same approach Habitat uses in ShortestPathFollower.
        It uses navmesh pathfinding for proper obstacle avoidance.
        """
        if self.path_follower is None:
            print("[NAV] Path follower not initialized!")
            return None
        
        # Check distance to goal first
        agent_state = self.agent.get_state()
        current_pos = np.array(agent_state.position)
        goal = np.array(goal_pos)
        distance = np.linalg.norm(goal - current_pos)
        
        print(f"[NAV] Distance to goal: {distance:.2f}m")
        
        if distance < 0.5:
            print("[NAV] Goal reached!")
            return None
        
        try:
            # Use GreedyGeodesicFollower to get next action
            # This handles all the navmesh pathfinding automatically
            next_action = self.path_follower.next_action_along(goal_pos)
            
            if next_action == "stop" or next_action is None:
                print("[NAV] Follower says stop/None - goal reached or unreachable")
                return None
            
            print(f"[NAV] GreedyFollower action: {next_action}")
            return next_action
            
        except GreedyFollowerError as e:
            print(f"[NAV] GreedyFollowerError: {e}")
            return None
        except Exception as e:
            print(f"[NAV] Error getting action: {e}")
            return None
    
    def get_semantic_objects(self) -> Dict[str, List[Tuple[int, str]]]:
        """Get list of objects in scene with their semantic IDs."""
        objects = {}
        scene = self.sim.semantic_scene
        
        if scene is None:
            return objects
        
        for obj in scene.objects:
            if obj is None:
                continue
            category = obj.category
            if category is None:
                continue
            name = category.name().lower()
            if name not in objects:
                objects[name] = []
            objects[name].append((obj.semantic_id, name))
        
        return objects

    def get_object_positions(self) -> Dict[str, List[mn.Vector3]]:
        """Get positions of semantic objects in the scene."""
        positions = {}
        scene = self.sim.semantic_scene
        
        if scene is None:
            print("No semantic scene available")
            return positions
        
        for obj in scene.objects:
            if obj is None:
                continue
            category = obj.category
            if category is None:
                continue
            name = category.name().lower()
            
            # Get object center (AABB center)
            aabb = obj.aabb
            if aabb is not None:
                center = aabb.center
                # Snap to navmesh if possible
                nav_point = self.sim.pathfinder.snap_point(center)
                if not np.isnan(nav_point[0]):
                    if name not in positions:
                        positions[name] = []
                    positions[name].append(mn.Vector3(nav_point))
        
        return positions
    
    def get_room_positions(self) -> Dict[str, List[mn.Vector3]]:
        """Get navigable positions in different rooms."""
        rooms = {}
        scene = self.sim.semantic_scene
        
        if scene is None:
            return rooms
        
        for region in scene.regions:
            if region is None:
                continue
            category = region.category
            if category is None:
                continue
            name = category.name().lower()
            
            # Get region center
            aabb = region.aabb
            if aabb is not None:
                center = aabb.center
                nav_point = self.sim.pathfinder.snap_point(center)
                if not np.isnan(nav_point[0]):
                    if name not in rooms:
                        rooms[name] = []
                    rooms[name].append(mn.Vector3(nav_point))
        
        return rooms
    
    def load_hssd_semantics(self, scene_path: str) -> Tuple[Dict[str, List[mn.Vector3]], Dict[str, List[mn.Vector3]]]:
        """Load semantics from HSSD semantic config files."""
        rooms = {}
        objects = {}
        
        # Get scene ID from path
        scene_name = os.path.basename(scene_path)
        scene_id = scene_name.split('.')[0]  # e.g., "102343992"
        
        # Look for semantic config
        data_path = os.path.join(PROJECT_ROOT, "data", "scene_datasets", "hssd-hab")
        semantic_config_path = os.path.join(data_path, "semantics", "scenes", f"{scene_id}.semantic_config.json")
        
        if not os.path.exists(semantic_config_path):
            print(f"No semantic config found at {semantic_config_path}")
            return rooms, objects
        
        try:
            with open(semantic_config_path, 'r') as f:
                semantic_data = json.load(f)
        except Exception as e:
            print(f"Error loading semantic config: {e}")
            return rooms, objects
        
        # Extract room/region annotations
        if "region_annotations" in semantic_data:
            for region in semantic_data["region_annotations"]:
                name = region.get("name", "unknown").lower()
                
                # Get center of region from bounds
                min_bounds = region.get("min_bounds")
                max_bounds = region.get("max_bounds")
                
                if min_bounds and max_bounds:
                    center = [
                        (min_bounds[0] + max_bounds[0]) / 2,
                        (min_bounds[1] + max_bounds[1]) / 2,
                        (min_bounds[2] + max_bounds[2]) / 2
                    ]
                    
                    # Snap to navmesh
                    nav_point = self.sim.pathfinder.snap_point(center)
                    if not np.isnan(nav_point[0]):
                        if name not in rooms:
                            rooms[name] = []
                        rooms[name].append(mn.Vector3(nav_point))
                        print(f"  Found room: {name}")
        
        print(f"Loaded {len(rooms)} room types from semantic config")
        return rooms, objects
    
    def get_scene_objects_from_rigid_objects(self) -> Dict[str, List[mn.Vector3]]:
        """Get objects from the simulator's rigid object manager."""
        objects = {}
        
        rom = self.sim.get_rigid_object_manager()
        
        for obj_id in rom.get_object_handles():
            obj = rom.get_object_by_handle(obj_id)
            if obj is not None:
                # Get object name from handle
                handle_name = obj_id.split('/')[-1].split('.')[0].lower()
                
                # Only include recognizable furniture/appliance keywords
                recognized_name = None
                for keyword in ['chair', 'table', 'sofa', 'couch', 'bed', 'desk', 'cabinet', 
                               'refrigerator', 'fridge', 'tv', 'television', 'lamp', 
                               'toilet', 'sink', 'bathtub', 'shower', 'oven', 'microwave']:
                    if keyword in handle_name:
                        recognized_name = keyword
                        break
                
                # Skip unrecognized objects (like random asset IDs)
                if recognized_name is None:
                    continue
                
                pos = obj.translation
                nav_point = self.sim.pathfinder.snap_point(pos)
                
                if not np.isnan(nav_point[0]):
                    if recognized_name not in objects:
                        objects[recognized_name] = []
                    objects[recognized_name].append(mn.Vector3(nav_point))
        
        return objects
    
    def init_llm_client(self):
        """Initialize LLM client for task generation."""
        try:
            config = LLMConfig()
            if config.validate():
                self.llm_client = LLMClient(config)
                self.use_llm = True
                print("LLM client initialized for task generation")
            else:
                print("LLM config invalid, using random task selection")
                self.use_llm = False
        except Exception as e:
            print(f"Failed to init LLM: {e}, using random task selection")
            self.use_llm = False
    
    def init_vesper_integration(self, rooms: List[str], room_positions: Optional[Dict[str, Tuple[float, float, float]]] = None):
        """Initialize VESPER integration with IoT devices and humanoid.
        
        Args:
            rooms: List of room names detected in the scene
            room_positions: Optional dict of room name -> navigable position (x, y, z)
        """
        try:
            scene_id = "unknown"
            if self.sim and hasattr(self.sim, 'config') and hasattr(self.sim.config, 'scene_id'):
                scene_id = os.path.basename(self.sim.config.scene_id).split('.')[0]
            
            config = VesperConfig(
                enable_iot=True,
                enable_humanoid=True,
                enable_llm=self.use_llm,
            )
            
            self.vesper = VesperIntegration(config)
            self.vesper.scene_id = scene_id
            
            # Initialize IoT devices
            if rooms:
                self.vesper.init_iot(rooms, room_positions)
                
                # Set room positions in IoT bridge for motion detection
                if room_positions and self.vesper.iot_bridge:
                    self.vesper.iot_bridge.room_positions = room_positions
                    print(f"[VESPER] Set positions for {len(room_positions)} rooms")
                
                print(f"[VESPER] IoT initialized with {len(rooms)} rooms")
                
                # Print device summary for confirmation
                room_devices = self.vesper.iot_manager.get_room_devices_summary()
                total_devices = len(self.vesper.iot_manager.devices)
                print(f"[VESPER] Created {total_devices} IoT devices:")
                for room, devices in list(room_devices.items())[:5]:
                    print(f"  {room}: {', '.join(devices)}")
                if len(room_devices) > 5:
                    print(f"  ... and {len(room_devices) - 5} more rooms")
                
                # Print automation rules
                if self.vesper.iot_bridge:
                    automations = len(self.vesper.iot_bridge.automation_rules)
                    print(f"[VESPER] Created {automations} automation rules (motion -> lights)")
            
            # Initialize humanoid (tracks position)
            agent_state = self.agent.get_state()
            self.vesper.init_humanoid(
                sim=self.sim,
                initial_position=tuple(agent_state.position),
            )
            print("[VESPER] Humanoid controller initialized")
            
            # Share LLM client if available
            if self.use_llm and self.llm_client:
                self.vesper._llm_client = self.llm_client
            
            print(f"[VESPER] Integration initialized for scene: {scene_id}")
            
        except Exception as e:
            print(f"[VESPER] Failed to initialize: {e}")
            import traceback
            traceback.print_exc()
            self.vesper = None
    
    def get_room_devices_from_vesper(self) -> Dict[str, List[str]]:
        """Get room device summary from VESPER IoT manager."""
        if self.vesper and self.vesper.iot_manager:
            return self.vesper.iot_manager.get_room_devices_summary()
        return {}
    
    def print_iot_status(self):
        """Print current IoT device status."""
        if not self.vesper or not self.vesper.iot_manager:
            print("[VESPER] IoT not initialized")
            return
        
        print("\n=== VESPER IoT Device Status ===")
        for device_id, device in self.vesper.iot_manager.devices.items():
            state_icon = "🟢" if device.state == "on" else "⚪"
            print(f"  {state_icon} {device_id}: {device.device_type} in {device.room} [{device.state}]")
        print(f"Total: {len(self.vesper.iot_manager.devices)} devices")
        print("================================\n")
    
    def generate_llm_task(self, available_rooms: List[str], room_devices: Optional[Dict[str, List[str]]] = None) -> Tuple[str, str]:
        """Generate a navigation task using LLM with scene context.
        
        Args:
            available_rooms: List of room names in the current scene
            room_devices: Optional dict of room -> list of devices in that room
        
        Returns:
            (task_description, target_room): e.g. ("Go check the kitchen", "kitchen")
        """
        if not self.use_llm or not self.llm_client:
            # Fallback to random selection
            target = random.choice(available_rooms)
            task = f"Navigate to the {target}"
            return task, target
        
        # Build context about the house
        scene_id = "unknown"
        if self.sim and hasattr(self.sim, 'config') and hasattr(self.sim.config, 'scene_id'):
            scene_id = os.path.basename(self.sim.config.scene_id).split('.')[0]
        
        # Prepare room list with devices if available
        if room_devices:
            room_info = []
            for room in available_rooms:
                devices = room_devices.get(room, [])
                if devices:
                    device_str = ", ".join(devices)
                    room_info.append(f"{room} (devices: {device_str})")
                else:
                    room_info.append(room)
            rooms_description = "\n".join([f"- {info}" for info in room_info])
        else:
            rooms_description = "\n".join([f"- {room}" for room in available_rooms])
        
        # Prepare prompt for LLM with scene context
        prompt = f"""You are a smart home assistant helping a user navigate their home.

Scene: {scene_id}
Available rooms and devices:
{rooms_description}

Generate ONE realistic navigation command that a user might give you. Examples:
- "Go to the toilet"
- "Check the kitchen"
- "Navigate to the bedroom" 
- "Head to the living room"
- "Check if the motion sensor in the bathroom is working"
- "Go see the smart lights in the bedroom"

Choose ONE room from the available list and create a natural, conversational command.

Respond ONLY with JSON in this exact format:
{{"command": "your command here", "target": "room_name"}}

The target MUST be exactly one of: {", ".join(available_rooms)}"""

        try:
            # Create proper LLMMessage object (it's a dataclass)
            message = LLMMessage(role="user", content=prompt)
            
            print(f"[LLM] Sending request to LLM...")
            response = self.llm_client.chat([message], temperature=0.8, max_tokens=100)
            print(f"[LLM] Raw response: {response.content}")
            
            # Parse JSON from response content
            import re
            json_match = re.search(r'\{[^}]+\}', response.content)
            if json_match:
                json_str = json_match.group()
                print(f"[LLM] Extracted JSON: {json_str}")
                result = json.loads(json_str)
                command = result.get("command", "Navigate to location")
                target = result.get("target", available_rooms[0])
                
                print(f"[LLM] Parsed - command: '{command}', target: '{target}'")
                
                # Validate target is in available rooms
                if target not in available_rooms:
                    print(f"[LLM] Target '{target}' not in available rooms, finding closest match...")
                    # Try to find closest match
                    for room in available_rooms:
                        if room in target.lower() or target.lower() in room:
                            print(f"[LLM] Matched '{target}' to '{room}'")
                            target = room
                            break
                    else:
                        print(f"[LLM] No match found, using first room: {available_rooms[0]}")
                        target = available_rooms[0]
                
                print(f"[LLM Task] '{command}' -> {target}")
                return command, target
            else:
                print(f"[LLM] No JSON found in response")
                raise ValueError("No JSON in response")
                
        except Exception as e:
            print(f"LLM task generation failed: {e}, using fallback")
            target = random.choice(available_rooms)
            task = f"Navigate to the {target}"
            return task, target


class GameUI:
    """Pygame UI for ObjectNav demo."""
    
    def __init__(self, nav_demo: ObjectNavDemo):
        self.nav_demo = nav_demo
        self.screen = None
        self.clock = pygame.time.Clock()
        self.font = None
        self.small_font = None
        
        # UI State
        self.show_help = True
        self.show_map = False
        self.auto_navigate = False
        self.goal_name = None
        self.goal_pos = None
        self.status_message = "Press T to set room goal, N to auto-navigate"
        self.available_targets = []
        self.third_person = False  # Start in first-person view
        self.show_iot_panel = False  # Toggle IoT device panel
        
    def init_pygame(self):
        pygame.init()
        pygame.display.set_caption("VESPER ObjectNav - Navigate to Rooms")
        self.screen = pygame.display.set_mode(RESOLUTION)
        self.font = pygame.font.Font(None, 36)
        self.small_font = pygame.font.Font(None, 24)
        
    def render_frame(self, observations: dict):
        """Render the current frame."""
        # Get RGB observation (first or third person)
        if self.third_person and "third_rgb" in observations:
            rgb = observations["third_rgb"]
        else:
            rgb = observations["rgb"]
        
        # Convert to pygame surface
        # RGB is (H, W, 4) with RGBA
        rgb = rgb[:, :, :3]  # Remove alpha
        rgb = np.ascontiguousarray(rgb)
        surface = pygame.surfarray.make_surface(rgb.swapaxes(0, 1))
        
        # Blit to screen
        self.screen.blit(surface, (0, 0))
        
        # Draw UI overlay
        self._draw_overlay()
        
        pygame.display.flip()
        self.clock.tick(60)
        
    def _draw_overlay(self):
        """Draw UI overlay."""
        # Semi-transparent panel at top
        panel = pygame.Surface((RESOLUTION[0], 80), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 150))
        self.screen.blit(panel, (0, 0))
        
        # Title
        title = self.font.render("VESPER ObjectNav + LLM", True, (0, 255, 255))
        self.screen.blit(title, (20, 10))
        
        # Status message
        status = self.small_font.render(self.status_message, True, (255, 255, 255))
        self.screen.blit(status, (20, 35))
        
        # Current task (LLM-generated command)
        if self.nav_demo.current_task:
            task_text = self.font.render(f'"{self.nav_demo.current_task}"', True, (255, 215, 0))
            self.screen.blit(task_text, (20, 55))
        
        # Goal indicator
        if self.goal_name:
            goal_text = self.font.render(f"Goal: {self.goal_name}", True, (0, 255, 0))
            self.screen.blit(goal_text, (RESOLUTION[0] - 300, 10))
        
        # Auto-nav indicator
        if self.auto_navigate:
            auto_text = self.small_font.render("[AUTO-NAV ON]", True, (255, 200, 0))
            self.screen.blit(auto_text, (RESOLUTION[0] - 300, 35))
        
        # Help panel
        if self.show_help:
            self._draw_help()
        
        # IoT device panel
        if self.show_iot_panel:
            self._draw_iot_panel()
        
        # Config menu (render on top)
        if self.nav_demo.vesper and self.nav_demo.vesper.config_menu:
            config_menu = self.nav_demo.vesper.config_menu
            if config_menu.is_visible:
                config_menu.render(self.screen, RESOLUTION[0], RESOLUTION[1])
    
    def _draw_iot_panel(self):
        """Draw IoT device status panel with live states and events."""
        if not self.nav_demo.vesper:
            return
        
        vesper = self.nav_demo.vesper
        manager = vesper.iot_manager
        bridge = vesper.iot_bridge
        
        if not manager and not bridge:
            return
        
        # Calculate panel size - larger for events
        panel_height = 550
        panel_width = 340
        
        iot_panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        iot_panel.fill((0, 0, 50, 230))
        
        # Title with stats
        title = self.font.render("IoT Devices", True, (0, 255, 255))
        iot_panel.blit(title, (10, 10))
        
        # Stats from bridge
        y = 40
        if bridge:
            stats = bridge.stats
            stats_text = f"Motion: {stats['motion_events']} | Auto: {stats['automation_triggers']}"
            stats_render = self.small_font.render(stats_text, True, (100, 200, 255))
            iot_panel.blit(stats_render, (10, y))
            y += 20
            
            if stats.get('current_room'):
                room_text = self.small_font.render(f"Current room: {stats['current_room']}", True, (255, 200, 0))
                iot_panel.blit(room_text, (10, y))
                y += 20
        
        # Separator
        pygame.draw.line(iot_panel, (100, 100, 100), (10, y), (panel_width - 10, y), 1)
        y += 10
        
        # Room-by-room device list with live states from bridge
        devices_to_show = bridge.devices if bridge else (manager.devices if manager else {})
        rooms_shown = {}
        
        for device_id, device in devices_to_show.items():
            if hasattr(device, 'room'):
                room = device.room
            else:
                room = device_id.rsplit('_', 1)[0]
            
            if room not in rooms_shown:
                rooms_shown[room] = []
            rooms_shown[room].append(device)
        
        for room, devices in list(rooms_shown.items())[:5]:
            # Room name
            room_text = self.small_font.render(f"[{room.title()}]", True, (255, 200, 0))
            iot_panel.blit(room_text, (10, y))
            y += 20
            
            # Devices in this room
            for device in devices[:4]:
                if hasattr(device, 'device_type'):
                    dev_type = device.device_type.replace('_', ' ')
                    state = device.state if hasattr(device, 'state') else "off"
                    is_triggered = getattr(device, 'is_triggered', False)
                else:
                    dev_type = "device"
                    state = "off"
                    is_triggered = False
                
                # Color based on state
                if is_triggered or state in ["on", "triggered"]:
                    state_color = (0, 255, 0)
                    state_icon = "*"
                else:
                    state_color = (120, 120, 120)
                    state_icon = "o"
                
                dev_text = self.small_font.render(f"  {state_icon} {dev_type}", True, state_color)
                iot_panel.blit(dev_text, (15, y))
                y += 18
            
            y += 5
            if y > panel_height - 150:
                break
        
        # Recent events section
        pygame.draw.line(iot_panel, (100, 100, 100), (10, y), (panel_width - 10, y), 1)
        y += 5
        
        events_title = self.small_font.render("Recent Events:", True, (255, 150, 0))
        iot_panel.blit(events_title, (10, y))
        y += 20
        
        if bridge:
            recent_events = bridge.get_recent_events(5)
            for event in reversed(recent_events):
                event_type = event.get('type', 'unknown')
                room = event.get('room', '')
                
                # Format event
                if event_type == "motion_detected":
                    event_text = f"! Motion in {room}"
                    color = (255, 100, 100)
                elif event_type == "automation_triggered":
                    rule = event.get('data', {}).get('rule', 'unknown')
                    event_text = f"# {rule}"
                    color = (100, 255, 100)
                elif event_type == "room_enter":
                    event_text = f"> Entered {room}"
                    color = (100, 200, 255)
                else:
                    event_text = f"{event_type} in {room}"
                    color = (180, 180, 180)
                
                event_render = self.small_font.render(event_text[:35], True, color)
                iot_panel.blit(event_render, (15, y))
                y += 18
                
                if y > panel_height - 30:
                    break
        
        # Hint to close
        hint = self.small_font.render("Press I to close", True, (150, 150, 150))
        iot_panel.blit(hint, (10, panel_height - 25))
        
        # Position on left side of screen
        self.screen.blit(iot_panel, (10, 90))
    
    def _draw_help(self):
        """Draw help panel."""
        help_panel = pygame.Surface((280, 285), pygame.SRCALPHA)
        help_panel.fill((0, 0, 0, 180))
        
        help_lines = [
            "Controls:",
            "W/↑ - Move Forward",
            "S/↓ - Move Backward", 
            "A/← - Turn Left",
            "D/→ - Turn Right",
            "G - Set random goal",
            "T - Generate LLM task",
            "N - Auto-navigate to goal",
            "I - Show IoT devices",
            "C - Config menu",
            "L - Print event log",
            "V - Toggle 1st/3rd person",
            "H - Toggle help",
            "ESC - Quit"
        ]
        
        y = 10
        for line in help_lines:
            text = self.small_font.render(line, True, (255, 255, 255))
            help_panel.blit(text, (10, y))
            y += 22
        
        self.screen.blit(help_panel, (RESOLUTION[0] - 290, 70))
    
    def handle_events(self) -> Optional[str]:
        """Handle pygame events. Returns action or None."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "quit"
            elif event.type == pygame.MOUSEBUTTONDOWN:
                # Handle mouse clicks for config menu
                if event.button == 1:  # Left click
                    mouse_x, mouse_y = event.pos
                    if hasattr(self, 'nav_demo') and self.nav_demo.vesper:
                        config_menu = self.nav_demo.vesper.config_menu
                        if config_menu and config_menu.is_visible:
                            config_menu.handle_click(mouse_x, mouse_y)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return "quit"
                elif event.key == pygame.K_h:
                    self.show_help = not self.show_help
                elif event.key == pygame.K_g:
                    return "set_goal"
                elif event.key == pygame.K_t:
                    return "set_object_goal"
                elif event.key == pygame.K_n:
                    self.auto_navigate = not self.auto_navigate
                    if self.auto_navigate and self.goal_pos is None:
                        self.status_message = "Set a goal first with T"
                        self.auto_navigate = False
                elif event.key == pygame.K_i:
                    return "show_iot"
                elif event.key == pygame.K_l:
                    return "print_log"
                elif event.key == pygame.K_c:
                    return "config_menu"
                elif event.key == pygame.K_v:
                    self.third_person = not self.third_person
                    view_name = "Third-person" if self.third_person else "First-person"
                    self.status_message = f"{view_name} view"
                else:
                    # Pass other keys to config menu if it's open
                    if hasattr(self, 'nav_demo') and self.nav_demo.vesper:
                        config_menu = self.nav_demo.vesper.config_menu
                        if config_menu and config_menu.is_visible:
                            config_menu.handle_keypress(event.key)
        
        # Continuous key presses
        keys = pygame.key.get_pressed()
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            return "move_forward"
        elif keys[pygame.K_s] or keys[pygame.K_DOWN]:
            return "move_backward"
        elif keys[pygame.K_a] or keys[pygame.K_LEFT]:
            return "turn_left"
        elif keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            return "turn_right"
        elif keys[pygame.K_q]:
            return "look_up"
        elif keys[pygame.K_e]:
            return "look_down"
        
        return None
    
    def set_goal(self, name: str, pos: mn.Vector3, distance: float):
        """Set current navigation goal."""
        self.goal_name = name
        self.goal_pos = pos
        self.status_message = f"Navigate to {name} ({distance:.1f}m away)"
        
    def clear_goal(self):
        """Clear current goal."""
        self.goal_name = None
        self.goal_pos = None
        self.auto_navigate = False
        self.status_message = "Goal reached! Press G for new goal"


def main():
    """Main entry point."""
    print("=" * 60)
    print("VESPER ObjectNav Demo")
    print("First-person navigation in photorealistic indoor scenes")
    print("=" * 60)
    
    # Create demo
    demo = ObjectNavDemo()
    
    # Find and load scene
    print("\nFinding scene...")
    scene_path, config_path = demo.find_scene()
    print(f"Loading scene: {os.path.basename(scene_path)}")
    
    # Create simulator
    demo.sim = demo.create_simulator(scene_path, config_path)
    demo.agent = demo.sim.get_agent(0)
    
    # Initialize the GreedyGeodesicFollower for proper navigation
    demo.init_path_follower()
    
    # Initialize LLM client for task generation
    demo.init_llm_client()
    
    # Place agent at navigable point
    start_pos = demo.get_random_navigable_point()
    agent_state = habitat_sim.AgentState()
    agent_state.position = start_pos
    demo.agent.set_state(agent_state)
    
    print(f"Agent starting at: {start_pos}")
    
    # Get semantic info
    semantic_objects = demo.get_semantic_objects()
    object_positions = demo.get_object_positions()
    room_positions = demo.get_room_positions()
    
    # Try to load HSSD semantics
    if "hssd" in scene_path.lower():
        hssd_rooms, hssd_objects = demo.load_hssd_semantics(scene_path)
        room_positions.update(hssd_rooms)
        object_positions.update(hssd_objects)
    
    # Get objects from rigid object manager
    rigid_objects = demo.get_scene_objects_from_rigid_objects()
    object_positions.update(rigid_objects)
    
    # Combine objects and rooms
    all_targets = {**object_positions, **room_positions}
    
    print(f"Found {len(semantic_objects)} object categories in scene")
    for cat in list(semantic_objects.keys())[:10]:
        print(f"  - {cat}: {len(semantic_objects[cat])} instances")
    
    if room_positions:
        print(f"Found {len(room_positions)} room types:")
        for room in room_positions:
            print(f"  - {room}")
    
    # Convert room positions to tuples for IoT bridge
    room_pos_dict = {}
    for room, positions in room_positions.items():
        if positions:
            # Use first position for each room
            pos = positions[0]
            room_pos_dict[room] = (float(pos[0]), float(pos[1]), float(pos[2]))
    
    # Initialize VESPER integration with IoT devices and humanoid
    print("\nInitializing VESPER integration...")
    demo.init_vesper_integration(list(room_positions.keys()), room_pos_dict)
    
    # Create UI
    ui = GameUI(demo)
    ui.init_pygame()
    ui.available_targets = list(all_targets.keys()) if all_targets else []
    
    # Try to load humanoid for third-person view
    print("\nLoading humanoid...")
    demo.load_humanoid()
    
    print("\nStarting navigation demo...")
    print("Controls: WASD to move, T for room goal, N to auto-navigate, V to toggle view, ESC to quit")
    
    running = True
    while running:
        # Get observations
        obs = demo.sim.get_sensor_observations()
        
        # Update VESPER systems
        if demo.vesper:
            agent_state = demo.agent.get_state()
            agent_pos = tuple(agent_state.position)
            
            # Update humanoid position
            if demo.vesper.humanoid:
                quat = agent_state.rotation
                demo.vesper.update_humanoid(
                    agent_position=agent_pos,
                    agent_rotation=(quat.x, quat.y, quat.z, quat.w),
                )
            
            # Update IoT bridge - triggers motion sensors and automations
            iot_events = demo.vesper.update_agent_position(agent_pos)
            
            # Show motion events in UI
            for event in iot_events:
                if event.get("event_type") == "motion_detected":
                    room = event.get("room", "unknown")
                    ui.status_message = f"🔴 Motion detected in {room}!"
                    # Sync with IoT panel display
                    if demo.vesper.iot_manager:
                        device_id = f"{room}_motion_sensor"
                        if device_id in demo.vesper.iot_manager.devices:
                            demo.vesper.iot_manager.devices[device_id].state = "on"
        
        # Render
        ui.render_frame(obs)
        
        # Handle input
        action = ui.handle_events()
        
        if action == "quit":
            running = False
        elif action == "print_log":
            # Print IoT event log to terminal
            if demo.vesper and demo.vesper.iot_bridge:
                demo.vesper.iot_bridge.print_event_log(30)
                ui.status_message = "Event log printed to terminal"
            else:
                print("[IoT] Event log not available")
                ui.status_message = "IoT not initialized"
        elif action == "config_menu":
            # Toggle IoT config menu
            if demo.vesper and demo.vesper.config_menu:
                demo.vesper.config_menu.toggle_visibility()
                if demo.vesper.config_menu.is_visible:
                    ui.status_message = "Config Menu: Add devices & rules"
                else:
                    ui.status_message = "Config menu closed"
            else:
                ui.status_message = "Config menu not available"
        elif action == "show_iot":
            # Toggle IoT device panel on screen
            ui.show_iot_panel = not ui.show_iot_panel
            if ui.show_iot_panel:
                if demo.vesper and demo.vesper.iot_manager:
                    device_count = len(demo.vesper.iot_manager.devices)
                    ui.status_message = f"IoT Panel: {device_count} devices"
                else:
                    ui.status_message = "IoT not initialized"
            else:
                ui.status_message = "IoT panel closed"
        elif action == "set_goal":
            # Set random navigable point as goal
            goal = demo.get_random_navigable_point()
            agent_state = demo.agent.get_state()
            distance = np.linalg.norm(
                np.array(goal) - np.array(agent_state.position)
            )
            ui.set_goal("Random Location", goal, distance)
            ui.goal_pos = goal
        elif action == "set_object_goal":
            # Use LLM to generate task or fall back to random
            if room_positions:
                available_rooms = list(room_positions.keys())
                
                # Get room devices from VESPER IoT manager
                room_devices = demo.get_room_devices_from_vesper()
                
                # Generate task with LLM (includes scene and device context)
                task_description, target_type = demo.generate_llm_task(available_rooms, room_devices)
                
                # Get position for target
                if target_type in room_positions:
                    target_pos = random.choice(room_positions[target_type])
                else:
                    # Fallback if LLM chose invalid room
                    target_type = random.choice(available_rooms)
                    target_pos = random.choice(room_positions[target_type])
                
                agent_state = demo.agent.get_state()
                distance = np.linalg.norm(
                    np.array(target_pos) - np.array(agent_state.position)
                )
                ui.set_goal(task_description, target_pos, distance)
                ui.goal_pos = target_pos
                demo.current_task = task_description
                print(f"[ObjectNav] Task: '{task_description}' -> {target_type} at {target_pos}, distance: {distance:.1f}m")
            elif all_targets:
                target_type = random.choice(list(all_targets.keys()))
                target_pos = random.choice(all_targets[target_type])
                agent_state = demo.agent.get_state()
                distance = np.linalg.norm(
                    np.array(target_pos) - np.array(agent_state.position)
                )
                task_description = f"Go to {target_type}"
                ui.set_goal(task_description, target_pos, distance)
                ui.goal_pos = target_pos
                demo.current_task = task_description
                print(f"[ObjectNav] Target: {target_type} at {target_pos}")
            else:
                ui.status_message = "No rooms or objects available"
                print("[ObjectNav] No targets available!")
        elif action in ["move_forward", "move_backward", "turn_left", "turn_right", 
                        "look_up", "look_down"]:
            demo.agent.act(action)
            demo.update_humanoid_position()  # Keep humanoid in sync
        
        # Auto-navigation runs independently (not in elif)
        if ui.auto_navigate and ui.goal_pos is not None and action is None:
            auto_action = demo.get_action_to_goal(ui.goal_pos)
            if auto_action:
                demo.agent.act(auto_action)
                demo.update_humanoid_position()
                # Update distance display
                agent_state = demo.agent.get_state()
                distance = np.linalg.norm(
                    np.array(ui.goal_pos) - np.array(agent_state.position)
                )
                ui.status_message = f"Navigating... {distance:.1f}m remaining"
            else:
                ui.clear_goal()
                print("[ObjectNav] Goal reached!")
    
    # Cleanup
    demo.sim.close()
    pygame.quit()
    print("\nDemo ended.")


if __name__ == "__main__":
    main()
