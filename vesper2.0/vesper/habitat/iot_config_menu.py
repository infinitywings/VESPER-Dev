"""
IoT Configuration Menu for adding devices and automation rules.

Provides an interactive UI overlay for:
- Adding new virtual devices to rooms (with clickable dropdowns)
- Creating automation rules between devices
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

if TYPE_CHECKING:
    from vesper.habitat.iot_bridge import IoTBridge

logger = logging.getLogger(__name__)


@dataclass
class ClickableRect:
    """A clickable rectangle region."""
    x: int
    y: int
    width: int
    height: int
    action: str
    data: Any = None
    
    def contains(self, mx: int, my: int) -> bool:
        """Check if point is inside rectangle."""
        return (self.x <= mx <= self.x + self.width and
                self.y <= my <= self.y + self.height)


class IoTConfigMenu:
    """
    Interactive menu for configuring IoT devices and automation rules.
    
    Features:
    - Add devices: click dropdown to select device type + room
    - Create automation rules: select trigger device + action device + action
    - Mouse click support for all interactions
    """
    
    DEVICE_TYPES = [
        "motion_sensor",
        "smart_light", 
        "temperature_sensor",
        "contact_sensor",
        "smart_door",
        "light_sensor",
        "leak_sensor",
        "humidity_sensor",
    ]
    
    AUTOMATION_ACTIONS = [
        "turn_on",
        "turn_off",
        "toggle",
    ]
    
    def __init__(self, iot_bridge: 'IoTBridge', available_rooms: List[str]):
        """
        Initialize the config menu.
        
        Args:
            iot_bridge: The IoT bridge to modify
            available_rooms: List of room names in the scene
        """
        self.iot_bridge = iot_bridge
        self.available_rooms = sorted(available_rooms)
        
        # Menu state
        self.active_tab = "devices"  # "devices" or "automations"
        self.is_visible = False
        
        # Selection indices
        self.device_type_index = 0
        self.room_index = 0
        self.trigger_device_index = 0
        self.action_device_index = 0
        self.action_type_index = 0
        
        # Device list for automations
        self.device_ids: List[str] = []
        
        # Clickable regions (populated during render)
        self.clickable_regions: List[ClickableRect] = []
        
        # Menu position (set during render)
        self.menu_x = 0
        self.menu_y = 0
        self.menu_width = 650
        self.menu_height = 620
        
        # Colors
        self.bg_color = (40, 40, 50, 230)
        self.header_color = (60, 60, 80)
        self.text_color = (255, 255, 255)
        self.highlight_color = (100, 150, 255)
        self.button_color = (70, 120, 200)
        self.button_hover_color = (90, 150, 230)
        self.dropdown_bg = (50, 55, 70)
        self.dropdown_hover = (70, 80, 100)
        
        if not PYGAME_AVAILABLE:
            logger.warning("pygame not available - IoT config menu disabled")
    
    def toggle_visibility(self):
        """Toggle menu visibility."""
        self.is_visible = not self.is_visible
        if self.is_visible:
            self._refresh_device_lists()
    
    def _refresh_device_lists(self):
        """Refresh the device list for automation creation."""
        self.device_ids = list(self.iot_bridge.devices.keys())
        # Reset indices if out of bounds
        if self.trigger_device_index >= len(self.device_ids):
            self.trigger_device_index = 0
        if self.action_device_index >= len(self.device_ids):
            self.action_device_index = 0
    
    def handle_click(self, mouse_x: int, mouse_y: int) -> bool:
        """
        Handle mouse click at position.
        
        Args:
            mouse_x: Mouse X position (screen coordinates)
            mouse_y: Mouse Y position (screen coordinates)
            
        Returns:
            True if click was handled
        """
        if not self.is_visible:
            return False
        
        # Check each clickable region
        for region in self.clickable_regions:
            if region.contains(mouse_x, mouse_y):
                return self._handle_action(region.action, region.data)
        
        return False
    
    def _handle_action(self, action: str, data: Any) -> bool:
        """Handle a menu action."""
        if action == "tab_devices":
            self.active_tab = "devices"
            return True
        
        elif action == "tab_automations":
            self.active_tab = "automations"
            self._refresh_device_lists()
            return True
        
        elif action == "device_type_prev":
            self.device_type_index = (self.device_type_index - 1) % len(self.DEVICE_TYPES)
            return True
        
        elif action == "device_type_next":
            self.device_type_index = (self.device_type_index + 1) % len(self.DEVICE_TYPES)
            return True
        
        elif action == "room_prev":
            if self.available_rooms:
                self.room_index = (self.room_index - 1) % len(self.available_rooms)
            return True
        
        elif action == "room_next":
            if self.available_rooms:
                self.room_index = (self.room_index + 1) % len(self.available_rooms)
            return True
        
        elif action == "add_device":
            self._add_device()
            return True
        
        elif action == "trigger_prev":
            if self.device_ids:
                self.trigger_device_index = (self.trigger_device_index - 1) % len(self.device_ids)
            return True
        
        elif action == "trigger_next":
            if self.device_ids:
                self.trigger_device_index = (self.trigger_device_index + 1) % len(self.device_ids)
            return True
        
        elif action == "action_device_prev":
            if self.device_ids:
                self.action_device_index = (self.action_device_index - 1) % len(self.device_ids)
            return True
        
        elif action == "action_device_next":
            if self.device_ids:
                self.action_device_index = (self.action_device_index + 1) % len(self.device_ids)
            return True
        
        elif action == "action_type_prev":
            self.action_type_index = (self.action_type_index - 1) % len(self.AUTOMATION_ACTIONS)
            return True
        
        elif action == "action_type_next":
            self.action_type_index = (self.action_type_index + 1) % len(self.AUTOMATION_ACTIONS)
            return True
        
        elif action == "add_rule":
            self._add_automation_rule()
            return True
        
        elif action == "close":
            self.is_visible = False
            return True
        
        return False
    
    def handle_keypress(self, key: int) -> bool:
        """Handle keyboard input."""
        if not PYGAME_AVAILABLE or not self.is_visible:
            return False
        
        # Close menu
        if key == pygame.K_c or key == pygame.K_ESCAPE:
            self.is_visible = False
            return True
        
        # Tab switching
        if key == pygame.K_TAB:
            self.active_tab = "automations" if self.active_tab == "devices" else "devices"
            if self.active_tab == "automations":
                self._refresh_device_lists()
            return True
        
        # Navigation
        if self.active_tab == "devices":
            if key == pygame.K_UP:
                self.device_type_index = (self.device_type_index - 1) % len(self.DEVICE_TYPES)
                return True
            if key == pygame.K_DOWN:
                self.device_type_index = (self.device_type_index + 1) % len(self.DEVICE_TYPES)
                return True
            if key == pygame.K_LEFT:
                if self.available_rooms:
                    self.room_index = (self.room_index - 1) % len(self.available_rooms)
                return True
            if key == pygame.K_RIGHT:
                if self.available_rooms:
                    self.room_index = (self.room_index + 1) % len(self.available_rooms)
                return True
            if key == pygame.K_RETURN:
                self._add_device()
                return True
        else:
            if key == pygame.K_UP:
                if self.device_ids:
                    self.trigger_device_index = (self.trigger_device_index - 1) % len(self.device_ids)
                return True
            if key == pygame.K_DOWN:
                if self.device_ids:
                    self.trigger_device_index = (self.trigger_device_index + 1) % len(self.device_ids)
                return True
            if key == pygame.K_LEFT:
                if self.device_ids:
                    self.action_device_index = (self.action_device_index - 1) % len(self.device_ids)
                return True
            if key == pygame.K_RIGHT:
                if self.device_ids:
                    self.action_device_index = (self.action_device_index + 1) % len(self.device_ids)
                return True
            if key == pygame.K_a:
                self.action_type_index = (self.action_type_index + 1) % len(self.AUTOMATION_ACTIONS)
                return True
            if key == pygame.K_RETURN:
                self._add_automation_rule()
                return True
        
        return False
    
    def _add_device(self):
        """Add a new device based on current selections."""
        if not self.available_rooms:
            logger.warning("No rooms available")
            return
        
        device_type = self.DEVICE_TYPES[self.device_type_index]
        room = self.available_rooms[self.room_index]
        
        # Get position for the device
        position = self._get_room_position(room)
        
        # Add device via IoT bridge
        device_id = self.iot_bridge.add_device(device_type, room, position)
        
        logger.info(f"[Config Menu] Added {device_type} to {room}: {device_id}")
        print(f"[Config Menu] ➕ Added {device_type} to {room}")
        
        # Refresh device lists
        self._refresh_device_lists()
    
    def _add_automation_rule(self):
        """Add a new automation rule based on current selections."""
        if not self.device_ids:
            logger.warning("No devices available for automation")
            return
        
        trigger_device_id = self.device_ids[self.trigger_device_index]
        action_device_id = self.device_ids[self.action_device_index]
        action_type = self.AUTOMATION_ACTIONS[self.action_type_index]
        
        # Get device info
        trigger_device = self.iot_bridge.devices.get(trigger_device_id)
        action_device = self.iot_bridge.devices.get(action_device_id)
        
        if not trigger_device or not action_device:
            logger.warning("Cannot add rule: device not found")
            return
        
        # Create rule name
        rule_name = f"custom_{trigger_device.device_type}_{action_type}_{len(self.iot_bridge.automation_rules)}"
        
        # Determine trigger event based on device type
        trigger_event = "motion_detected" if "motion" in trigger_device.device_type else "state_change"
        
        # Add rule via IoT bridge
        self.iot_bridge.add_automation_rule(
            name=rule_name,
            trigger_event=trigger_event,
            trigger_device_id=trigger_device_id,
            action=action_type,
            target_device_id=action_device_id,
        )
        
        logger.info(f"[Config Menu] Added automation: {trigger_device_id} -> {action_type} {action_device_id}")
        print(f"[Config Menu] ⚡ Added rule: {trigger_device_id} -> {action_type} {action_device_id}")
    
    def _get_room_position(self, room: str) -> Tuple[float, float, float]:
        """Get a position for a device in the given room."""
        # Look for existing devices in this room
        for device in self.iot_bridge.devices.values():
            if device.room == room:
                x, y, z = device.position
                return (x + 0.5, y, z + 0.5)
        return (0.0, 1.5, 0.0)
    
    def render(self, surface: Any, screen_width: int, screen_height: int):
        """
        Render the configuration menu with clickable elements.
        """
        if not PYGAME_AVAILABLE or not self.is_visible:
            return
        
        # Clear clickable regions
        self.clickable_regions = []
        
        # Calculate menu position (centered)
        self.menu_x = (screen_width - self.menu_width) // 2
        self.menu_y = (screen_height - self.menu_height) // 2
        
        # Create menu surface
        menu_surface = pygame.Surface((self.menu_width, self.menu_height), pygame.SRCALPHA)
        menu_surface.fill(self.bg_color)
        
        # Fonts
        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)
        
        # Header
        header_text = font_large.render("IoT Configuration Menu", True, self.text_color)
        menu_surface.blit(header_text, (20, 15))
        
        # Tab buttons
        tab_y = 55
        tab_width = 150
        tab_height = 35
        
        # Devices tab
        devices_color = self.highlight_color if self.active_tab == "devices" else self.header_color
        pygame.draw.rect(menu_surface, devices_color, (20, tab_y, tab_width, tab_height), border_radius=5)
        devices_text = font_medium.render("Devices", True, self.text_color)
        menu_surface.blit(devices_text, (55, tab_y + 7))
        self._add_clickable(20, tab_y, tab_width, tab_height, "tab_devices")
        
        # Automations tab
        automations_color = self.highlight_color if self.active_tab == "automations" else self.header_color
        pygame.draw.rect(menu_surface, automations_color, (180, tab_y, tab_width, tab_height), border_radius=5)
        automations_text = font_medium.render("Automations", True, self.text_color)
        menu_surface.blit(automations_text, (195, tab_y + 7))
        self._add_clickable(180, tab_y, tab_width, tab_height, "tab_automations")
        
        # Content area
        content_y = tab_y + 50
        
        if self.active_tab == "devices":
            self._render_devices_tab(menu_surface, font_medium, font_small, content_y)
        else:
            self._render_automations_tab(menu_surface, font_medium, font_small, content_y)
        
        # Close button
        close_btn_x = self.menu_width - 100
        close_btn_y = self.menu_height - 45
        pygame.draw.rect(menu_surface, (150, 60, 60), (close_btn_x, close_btn_y, 80, 30), border_radius=5)
        close_text = font_small.render("Close (C)", True, self.text_color)
        menu_surface.blit(close_text, (close_btn_x + 8, close_btn_y + 6))
        self._add_clickable(close_btn_x, close_btn_y, 80, 30, "close")
        
        # Blit to main surface
        surface.blit(menu_surface, (self.menu_x, self.menu_y))
    
    def _add_clickable(self, x: int, y: int, w: int, h: int, action: str, data: Any = None):
        """Add a clickable region (coordinates relative to menu)."""
        self.clickable_regions.append(ClickableRect(
            x=self.menu_x + x,
            y=self.menu_y + y,
            width=w,
            height=h,
            action=action,
            data=data
        ))
    
    def _render_dropdown_selector(
        self,
        surface: Any,
        font: Any,
        label: str,
        value: str,
        x: int,
        y: int,
        prev_action: str,
        next_action: str,
    ) -> int:
        """Render a dropdown-style selector with < value > buttons."""
        # Label
        label_text = font.render(label, True, self.text_color)
        surface.blit(label_text, (x, y))
        y += 28
        
        # Previous button
        btn_size = 30
        pygame.draw.rect(surface, self.dropdown_bg, (x, y, btn_size, btn_size), border_radius=3)
        prev_text = font.render("<", True, self.highlight_color)
        surface.blit(prev_text, (x + 10, y + 4))
        self._add_clickable(x, y, btn_size, btn_size, prev_action)
        
        # Value display
        value_width = 250
        pygame.draw.rect(surface, self.dropdown_bg, (x + btn_size + 5, y, value_width, btn_size), border_radius=3)
        # Truncate long values
        display_value = value[:25] + "..." if len(value) > 25 else value
        value_text = font.render(display_value, True, self.highlight_color)
        surface.blit(value_text, (x + btn_size + 15, y + 5))
        
        # Next button
        next_x = x + btn_size + 5 + value_width + 5
        pygame.draw.rect(surface, self.dropdown_bg, (next_x, y, btn_size, btn_size), border_radius=3)
        next_text = font.render(">", True, self.highlight_color)
        surface.blit(next_text, (next_x + 10, y + 4))
        self._add_clickable(next_x, y, btn_size, btn_size, next_action)
        
        return y + 45
    
    def _render_devices_tab(self, surface: Any, font_medium: Any, font_small: Any, start_y: int):
        """Render the device addition tab."""
        y = start_y
        x = 30
        
        # Title
        title = font_medium.render("Add New Device", True, self.text_color)
        surface.blit(title, (x, y))
        y += 40
        
        # Device type selector
        device_type = self.DEVICE_TYPES[self.device_type_index]
        y = self._render_dropdown_selector(
            surface, font_small, "Device Type:", device_type,
            x, y, "device_type_prev", "device_type_next"
        )
        
        # Room selector
        room = self.available_rooms[self.room_index] if self.available_rooms else "No rooms"
        y = self._render_dropdown_selector(
            surface, font_small, "Room:", room,
            x, y, "room_prev", "room_next"
        )
        
        y += 10
        
        # Add button
        btn_width = 200
        btn_height = 40
        pygame.draw.rect(surface, (50, 150, 80), (x, y, btn_width, btn_height), border_radius=5)
        add_text = font_medium.render("+ Add Device", True, self.text_color)
        surface.blit(add_text, (x + 40, y + 8))
        self._add_clickable(x, y, btn_width, btn_height, "add_device")
        
        y += 60
        
        # Stats
        device_count = len(self.iot_bridge.devices)
        stats_text = font_small.render(f"Current devices: {device_count}", True, (150, 150, 150))
        surface.blit(stats_text, (x, y))
        y += 30
        
        # Device list section
        pygame.draw.line(surface, (80, 80, 100), (x, y), (self.menu_width - 30, y), 1)
        y += 10
        
        list_title = font_small.render("Existing Devices:", True, (200, 200, 200))
        surface.blit(list_title, (x, y))
        y += 22
        
        # Show devices by room (scrollable area)
        devices_by_room: Dict[str, List] = {}
        for device in self.iot_bridge.devices.values():
            room = device.room
            if room not in devices_by_room:
                devices_by_room[room] = []
            devices_by_room[room].append(device)
        
        # Display up to 6 rooms
        max_display_y = start_y + 350
        for room, devices in list(devices_by_room.items())[:6]:
            if y > max_display_y:
                more_text = font_small.render("... more devices", True, (100, 100, 100))
                surface.blit(more_text, (x + 10, y))
                break
            
            # Room header
            room_text = font_small.render(f"[{room}]", True, (255, 200, 100))
            surface.blit(room_text, (x, y))
            y += 18
            
            # Devices in this room (up to 3)
            for device in devices[:3]:
                dev_type = device.device_type.replace('_', ' ')
                state_icon = "*" if device.state in ["on", "triggered"] else "o"
                state_color = (100, 255, 100) if device.state in ["on", "triggered"] else (120, 120, 120)
                dev_text = font_small.render(f"  {state_icon} {dev_type}", True, state_color)
                surface.blit(dev_text, (x + 10, y))
                y += 16
            
            if len(devices) > 3:
                more = font_small.render(f"  +{len(devices) - 3} more", True, (100, 100, 100))
                surface.blit(more, (x + 10, y))
                y += 16
            
            y += 5
    
    def _render_automations_tab(self, surface: Any, font_medium: Any, font_small: Any, start_y: int):
        """Render the automation rule creation tab."""
        y = start_y
        x = 30
        
        # Title
        title = font_medium.render("Create Automation Rule", True, self.text_color)
        surface.blit(title, (x, y))
        y += 40
        
        # Check if we have devices
        if not self.device_ids:
            no_devices = font_small.render("No devices available. Add devices first!", True, (255, 200, 100))
            surface.blit(no_devices, (x, y))
            return
        
        # Trigger device selector
        trigger_device = self.device_ids[self.trigger_device_index] if self.device_ids else "None"
        y = self._render_dropdown_selector(
            surface, font_small, "Trigger Device:", trigger_device,
            x, y, "trigger_prev", "trigger_next"
        )
        
        # Action device selector
        action_device = self.device_ids[self.action_device_index] if self.device_ids else "None"
        y = self._render_dropdown_selector(
            surface, font_small, "Action Device:", action_device,
            x, y, "action_device_prev", "action_device_next"
        )
        
        # Action type selector
        action_type = self.AUTOMATION_ACTIONS[self.action_type_index]
        y = self._render_dropdown_selector(
            surface, font_small, "Action:", action_type,
            x, y, "action_type_prev", "action_type_next"
        )
        
        y += 10
        
        # Add button
        btn_width = 200
        btn_height = 40
        pygame.draw.rect(surface, (50, 120, 150), (x, y, btn_width, btn_height), border_radius=5)
        add_text = font_medium.render("+ Add Rule", True, self.text_color)
        surface.blit(add_text, (x + 50, y + 8))
        self._add_clickable(x, y, btn_width, btn_height, "add_rule")
        
        y += 60
        
        # Stats
        rule_count = len(self.iot_bridge.automation_rules)
        stats_text = font_small.render(f"Current rules: {rule_count}", True, (150, 150, 150))
        surface.blit(stats_text, (x, y))
        y += 30
        
        # Automation rules list section
        pygame.draw.line(surface, (80, 80, 100), (x, y), (self.menu_width - 30, y), 1)
        y += 10
        
        list_title = font_small.render("Existing Automation Rules:", True, (200, 200, 200))
        surface.blit(list_title, (x, y))
        y += 22
        
        # Display automation rules - use remaining menu height
        max_display_y = self.menu_height - 60
        for i, rule in enumerate(self.iot_bridge.automation_rules):
            if y > max_display_y:
                remaining = len(self.iot_bridge.automation_rules) - i
                more_text = font_small.render(f"... and {remaining} more rules", True, (100, 100, 100))
                surface.blit(more_text, (x + 10, y))
                break
            
            # Rule icon based on enabled state
            status_icon = "[ON]" if rule.enabled else "[OFF]"
            status_color = (100, 255, 100) if rule.enabled else (255, 100, 100)
            
            # Format rule display
            trigger_text = rule.trigger_room or rule.trigger_device_id or "any"
            target_text = rule.target_room or rule.target_device_id or "target"
            
            # Shorten if too long
            if len(trigger_text) > 12:
                trigger_text = trigger_text[:10] + ".."
            if len(target_text) > 12:
                target_text = target_text[:10] + ".."
            
            rule_display = f"{status_icon} {trigger_text} -> {rule.action} {target_text}"
            rule_text = font_small.render(rule_display, True, status_color)
            surface.blit(rule_text, (x, y))
            y += 20
