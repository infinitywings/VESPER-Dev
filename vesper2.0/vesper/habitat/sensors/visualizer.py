"""
Visual rendering utilities for sensor detection zones.

Provides pygame-based 2D overlays for:
- Motion sensor detection cones
- Camera FOV cones
- Sensor status indicators
"""

import math
from typing import List, Optional, Tuple

import numpy as np
import pygame


class SensorVisualizer:
    """
    Renders sensor detection zones as 2D overlays.
    
    Projects 3D sensor cones onto 2D pygame surface.
    """
    
    def __init__(self, screen_width: int, screen_height: int):
        """
        Initialize the sensor visualizer.
        
        Args:
            screen_width: Width of the pygame screen
            screen_height: Height of the pygame screen
        """
        self.screen_width = screen_width
        self.screen_height = screen_height
        
        # Colors
        self.MOTION_SENSOR_COLOR = (255, 165, 0, 80)  # Orange with alpha
        self.CAMERA_FOV_COLOR = (0, 255, 255, 60)     # Cyan with alpha
        self.DETECTION_ACTIVE_COLOR = (255, 0, 0, 120) # Red when detecting
        self.SENSOR_ICON_COLOR = (255, 255, 255)       # White for icons
    
    def world_to_screen(
        self,
        world_pos: Tuple[float, float, float],
        camera_pos: Tuple[float, float, float],
        camera_yaw: float,
        scale: float = 50.0,
    ) -> Optional[Tuple[int, int]]:
        """
        Convert 3D world position to 2D screen coordinates.
        
        Args:
            world_pos: (x, y, z) position in world
            camera_pos: (x, y, z) camera position
            camera_yaw: Camera yaw angle in radians
            scale: Pixels per meter
            
        Returns:
            (screen_x, screen_y) or None if behind camera
        """
        # Get relative position
        rel_x = world_pos[0] - camera_pos[0]
        rel_z = world_pos[2] - camera_pos[2]
        
        # Rotate by camera yaw
        cos_yaw = math.cos(-camera_yaw)
        sin_yaw = math.sin(-camera_yaw)
        
        rot_x = rel_x * cos_yaw - rel_z * sin_yaw
        rot_z = rel_x * sin_yaw + rel_z * cos_yaw
        
        # Check if in front of camera
        if rot_z < 0:
            return None
        
        # Project to screen
        screen_x = int(self.screen_width / 2 + rot_x * scale)
        screen_y = int(self.screen_height / 2 - rot_z * scale)
        
        return (screen_x, screen_y)
    
    def draw_motion_sensor_cone(
        self,
        surface: pygame.Surface,
        sensor_pos: Tuple[float, float, float],
        sensor_orientation: float,  # Yaw in radians
        detection_range: float,
        detection_angle: float,  # Degrees
        camera_pos: Tuple[float, float, float],
        camera_yaw: float,
        is_detecting: bool = False,
        scale: float = 50.0,
    ):
        """
        Draw motion sensor detection cone.
        
        Args:
            surface: pygame surface to draw on
            sensor_pos: Sensor position in world
            sensor_orientation: Sensor yaw angle
            detection_range: Detection range in meters
            detection_angle: Detection cone angle in degrees
            camera_pos: Camera position
            camera_yaw: Camera yaw
            is_detecting: Whether sensor is currently detecting
            scale: Pixels per meter
        """
        # Convert sensor position to screen
        sensor_screen = self.world_to_screen(sensor_pos, camera_pos, camera_yaw, scale)
        if not sensor_screen:
            return
        
        # Calculate cone points
        angle_rad = math.radians(detection_angle / 2)
        num_points = 20
        
        points = [sensor_screen]
        
        for i in range(num_points + 1):
            # Angle relative to sensor orientation
            theta = -angle_rad + (2 * angle_rad * i / num_points)
            world_angle = sensor_orientation + theta
            
            # Point at detection range
            point_x = sensor_pos[0] + detection_range * math.sin(world_angle)
            point_z = sensor_pos[2] + detection_range * math.cos(world_angle)
            
            point_screen = self.world_to_screen(
                (point_x, sensor_pos[1], point_z),
                camera_pos,
                camera_yaw,
                scale
            )
            
            if point_screen:
                points.append(point_screen)
        
        # Draw filled polygon if we have enough points
        if len(points) >= 3:
            # Create surface with alpha
            cone_surface = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
            
            color = self.DETECTION_ACTIVE_COLOR if is_detecting else self.MOTION_SENSOR_COLOR
            pygame.draw.polygon(cone_surface, color, points)
            
            # Draw outline
            outline_color = (255, 0, 0) if is_detecting else (255, 165, 0)
            pygame.draw.lines(cone_surface, outline_color, True, points, 2)
            
            surface.blit(cone_surface, (0, 0))
        
        # Draw sensor icon
        pygame.draw.circle(surface, self.SENSOR_ICON_COLOR, sensor_screen, 6)
        pygame.draw.circle(surface, (0, 0, 0), sensor_screen, 6, 2)
    
    def draw_camera_fov(
        self,
        surface: pygame.Surface,
        camera_sensor_pos: Tuple[float, float, float],
        pan: float,  # Radians
        tilt: float,  # Radians
        horizontal_fov: float,  # Degrees
        max_range: float,
        camera_pos: Tuple[float, float, float],
        camera_yaw: float,
        is_tracking: bool = False,
        scale: float = 50.0,
    ):
        """
        Draw security camera FOV cone.
        
        Args:
            surface: pygame surface to draw on
            camera_sensor_pos: Camera sensor position in world
            pan: Camera pan angle
            tilt: Camera tilt angle (for display purposes)
            horizontal_fov: Horizontal FOV in degrees
            max_range: Maximum detection range in meters
            camera_pos: Observer camera position
            camera_yaw: Observer camera yaw
            is_tracking: Whether camera is tracking a target
            scale: Pixels per meter
        """
        # Convert camera position to screen
        cam_screen = self.world_to_screen(camera_sensor_pos, camera_pos, camera_yaw, scale)
        if not cam_screen:
            return
        
        # Calculate FOV cone points
        fov_rad = math.radians(horizontal_fov / 2)
        num_points = 25
        
        points = [cam_screen]
        
        for i in range(num_points + 1):
            # Angle relative to camera pan
            theta = -fov_rad + (2 * fov_rad * i / num_points)
            world_angle = pan + theta
            
            # Point at max range
            point_x = camera_sensor_pos[0] + max_range * math.sin(world_angle)
            point_z = camera_sensor_pos[2] + max_range * math.cos(world_angle)
            
            point_screen = self.world_to_screen(
                (point_x, camera_sensor_pos[1], point_z),
                camera_pos,
                camera_yaw,
                scale
            )
            
            if point_screen:
                points.append(point_screen)
        
        # Draw filled polygon
        if len(points) >= 3:
            # Create surface with alpha
            fov_surface = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
            
            color = (0, 255, 0, 80) if is_tracking else self.CAMERA_FOV_COLOR
            pygame.draw.polygon(fov_surface, color, points)
            
            # Draw outline
            outline_color = (0, 255, 0) if is_tracking else (0, 255, 255)
            pygame.draw.lines(fov_surface, outline_color, True, points, 2)
            
            surface.blit(fov_surface, (0, 0))
        
        # Draw camera icon (triangle pointing in pan direction)
        icon_size = 8
        angle = pan - camera_yaw
        
        # Triangle points
        tip_offset_x = icon_size * math.sin(angle)
        tip_offset_z = icon_size * math.cos(angle)
        
        tip = (
            int(cam_screen[0] + tip_offset_x),
            int(cam_screen[1] - tip_offset_z)
        )
        
        left = (
            int(cam_screen[0] - icon_size * 0.5 * math.cos(angle)),
            int(cam_screen[1] - icon_size * 0.5 * math.sin(angle))
        )
        
        right = (
            int(cam_screen[0] + icon_size * 0.5 * math.cos(angle)),
            int(cam_screen[1] + icon_size * 0.5 * math.sin(angle))
        )
        
        pygame.draw.polygon(surface, self.SENSOR_ICON_COLOR, [tip, left, right])
        pygame.draw.polygon(surface, (0, 0, 0), [tip, left, right], 2)
    
    def draw_sensor_label(
        self,
        surface: pygame.Surface,
        sensor_pos: Tuple[float, float, float],
        label: str,
        camera_pos: Tuple[float, float, float],
        camera_yaw: float,
        font: pygame.font.Font,
        scale: float = 50.0,
    ):
        """
        Draw text label for a sensor.
        
        Args:
            surface: pygame surface to draw on
            sensor_pos: Sensor position in world
            label: Text to display
            camera_pos: Camera position
            camera_yaw: Camera yaw
            font: pygame font to use
            scale: Pixels per meter
        """
        screen_pos = self.world_to_screen(sensor_pos, camera_pos, camera_yaw, scale)
        if not screen_pos:
            return
        
        # Draw label above sensor
        text_surface = font.render(label, True, (255, 255, 255))
        text_rect = text_surface.get_rect()
        text_rect.center = (screen_pos[0], screen_pos[1] - 20)
        
        # Draw background
        bg_rect = text_rect.inflate(8, 4)
        pygame.draw.rect(surface, (0, 0, 0, 180), bg_rect)
        pygame.draw.rect(surface, (255, 255, 255), bg_rect, 1)
        
        surface.blit(text_surface, text_rect)
    
    def draw_detection_indicator(
        self,
        surface: pygame.Surface,
        sensor_pos: Tuple[float, float, float],
        target_pos: Tuple[float, float, float],
        camera_pos: Tuple[float, float, float],
        camera_yaw: float,
        scale: float = 50.0,
    ):
        """
        Draw line from sensor to detected target.
        
        Args:
            surface: pygame surface to draw on
            sensor_pos: Sensor position
            target_pos: Target position
            camera_pos: Camera position
            camera_yaw: Camera yaw
            scale: Pixels per meter
        """
        sensor_screen = self.world_to_screen(sensor_pos, camera_pos, camera_yaw, scale)
        target_screen = self.world_to_screen(target_pos, camera_pos, camera_yaw, scale)
        
        if sensor_screen and target_screen:
            # Draw dashed line
            pygame.draw.line(surface, (255, 0, 0), sensor_screen, target_screen, 2)
            
            # Draw pulsing circle at target
            pulse_size = int(8 + 4 * math.sin(pygame.time.get_ticks() / 200))
            pygame.draw.circle(surface, (255, 0, 0, 100), target_screen, pulse_size, 3)


class AvatarRenderer:
    """
    Renders humanoid avatar in third-person view.
    """
    
    def __init__(self):
        """Initialize the avatar renderer."""
        self.avatar_color = (0, 150, 255)  # Blue
        self.head_color = (255, 200, 180)  # Skin tone
        self.height = 1.7  # meters
        self.body_width = 0.4
    
    def draw_simple_avatar(
        self,
        surface: pygame.Surface,
        avatar_pos: Tuple[float, float, float],
        avatar_yaw: float,
        camera_pos: Tuple[float, float, float],
        camera_yaw: float,
        visualizer: SensorVisualizer,
        scale: float = 50.0,
    ):
        """
        Draw simple 2D avatar representation.
        
        Args:
            surface: pygame surface to draw on
            avatar_pos: Avatar position in world
            avatar_yaw: Avatar facing direction
            camera_pos: Camera position
            camera_yaw: Camera yaw
            visualizer: SensorVisualizer for projection
            scale: Pixels per meter
        """
        # Get screen position
        screen_pos = visualizer.world_to_screen(avatar_pos, camera_pos, camera_yaw, scale)
        if not screen_pos:
            return
        
        # Draw body (circle for simplicity in top-down)
        body_radius = int(self.body_width * scale / 2)
        pygame.draw.circle(surface, self.avatar_color, screen_pos, body_radius)
        pygame.draw.circle(surface, (0, 0, 0), screen_pos, body_radius, 2)
        
        # Draw facing direction indicator
        direction_angle = avatar_yaw - camera_yaw
        indicator_length = body_radius + 10
        
        end_x = int(screen_pos[0] + indicator_length * math.sin(direction_angle))
        end_y = int(screen_pos[1] - indicator_length * math.cos(direction_angle))
        
        pygame.draw.line(surface, (255, 255, 0), screen_pos, (end_x, end_y), 3)
        
        # Draw head (small circle)
        head_offset_x = int((body_radius + 5) * math.sin(direction_angle))
        head_offset_y = int(-(body_radius + 5) * math.cos(direction_angle))
        
        head_pos = (screen_pos[0] + head_offset_x, screen_pos[1] + head_offset_y)
        pygame.draw.circle(surface, self.head_color, head_pos, 6)
        pygame.draw.circle(surface, (0, 0, 0), head_pos, 6, 1)
