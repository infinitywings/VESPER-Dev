"""
VESPER HUD - Pygame overlay for the simulation.

Displays IoT device status, current task, and event log.
"""

from typing import Any, Dict, List

import numpy as np

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False


class VesperHUD:
    """Heads-up display for VESPER simulation."""
    
    def __init__(self, width: int, height: int):
        if not PYGAME_AVAILABLE:
            raise ImportError("pygame is required for VesperHUD")
        
        self.width = width
        self.height = height
        self.font = pygame.font.Font(None, 20)
        self.title_font = pygame.font.Font(None, 26)
        self.task_font = pygame.font.Font(None, 22)
        self.show_overlay = True
        self.show_help = False
    
    def render(
        self,
        screen: pygame.Surface,
        device_states: List[Dict[str, Any]],
        event_log: List[str],
        agent_pos: np.ndarray,
        fps: float,
        task_status: Dict[str, Any],
        mqtt_connected: bool = False,
        is_humanoid: bool = True,
    ):
        """Render the full HUD overlay."""
        if not self.show_overlay:
            return
        
        self._draw_top_bar(screen, agent_pos, fps, mqtt_connected, is_humanoid)
        self._draw_device_panel(screen, device_states)
        self._draw_task_panel(screen, task_status)
        self._draw_event_log(screen, event_log)
        self._draw_controls(screen, is_humanoid)
        
        if self.show_help:
            self._draw_help(screen)
    
    def _draw_top_bar(self, screen, agent_pos, fps, mqtt_connected, is_humanoid):
        """Draw top status bar."""
        bar = pygame.Surface((self.width, 35), pygame.SRCALPHA)
        bar.fill((0, 0, 0, 200))
        screen.blit(bar, (0, 0))
        
        agent_type = "Humanoid" if is_humanoid else "Robot"
        title = self.title_font.render(f"VESPER Smart Home - {agent_type}", True, (100, 200, 255))
        screen.blit(title, (10, 8))
        
        mqtt_text = "MQTT ●" if mqtt_connected else "MQTT ○"
        mqtt_color = (100, 255, 100) if mqtt_connected else (100, 100, 100)
        screen.blit(self.font.render(mqtt_text, True, mqtt_color), (self.width - 250, 10))
        
        pos_text = f"({agent_pos[0]:.1f}, {agent_pos[2]:.1f})"
        screen.blit(self.font.render(pos_text, True, (200, 200, 200)), (self.width - 170, 10))
        
        screen.blit(self.font.render(f"{fps:.0f} FPS", True, (200, 200, 200)), (self.width - 70, 10))
    
    def _draw_device_panel(self, screen, device_states):
        """Draw IoT device status panel."""
        panel_width = 180
        panel_height = min(300, 40 + len(device_states) * 20)
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 180))
        screen.blit(panel, (10, 45))
        
        screen.blit(self.font.render("IoT Devices", True, (255, 255, 255)), (20, 52))
        
        y = 72
        for state in device_states:
            name = state.get("name", "Unknown")[:16]
            device_type = state.get("device_type", "")
            
            if device_type == "motion_sensor":
                detected = state.get("motion_detected", False)
                color = (255, 80, 80) if detected else (80, 80, 80)
                status = "●" if detected else "○"
            elif device_type == "smart_door":
                is_open = state.get("is_open", False)
                color = (100, 255, 100) if is_open else (255, 180, 80)
                status = "Open" if is_open else "Locked"
            elif device_type == "contact_sensor":
                is_open = state.get("is_open", False)
                color = (100, 255, 100) if is_open else (120, 120, 120)
                status = "●" if is_open else "○"
            elif device_type == "light_sensor":
                level = state.get("light_level", 0)
                color = (255, 255, 150)
                status = f"{level:.0f}lx"
            else:
                color = (150, 150, 150)
                status = "OK"
            
            screen.blit(self.font.render(f"{status} {name}", True, color), (20, y))
            y += 18
            if y > 330:
                break
    
    def _draw_task_panel(self, screen, task_status):
        """Draw current task panel."""
        panel_width = 300
        panel_height = 110
        panel_x = self.width - panel_width - 10
        panel_y = 45
        
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 180))
        screen.blit(panel, (panel_x, panel_y))
        
        screen.blit(self.font.render("Current Task", True, (255, 255, 255)), (panel_x + 10, panel_y + 8))
        
        task = task_status.get("current_task", "Idle - waiting for task...")
        state = task_status.get("execution_state", "idle")
        target = task_status.get("current_target")
        
        if task:
            # Word wrap the task text
            words = task.split()
            lines = []
            current_line = ""
            for word in words:
                test = current_line + " " + word if current_line else word
                if len(test) > 38:
                    lines.append(current_line)
                    current_line = word
                else:
                    current_line = test
            if current_line:
                lines.append(current_line)
            
            y = panel_y + 28
            for line in lines[:2]:
                color = (255, 220, 100) if not task_status.get("is_idle") else (150, 150, 150)
                screen.blit(self.task_font.render(line, True, color), (panel_x + 10, y))
                y += 18
        
        # Show execution state with color coding
        state_colors = {
            "idle": (150, 150, 150),
            "moving": (100, 200, 255),
            "executing": (255, 200, 100),
            "completed": (100, 255, 100),
        }
        state_color = state_colors.get(state, (150, 150, 150))
        state_text = f"State: {state.title()}"
        screen.blit(self.font.render(state_text, True, state_color), (panel_x + 10, panel_y + 68))
        
        # Show target if moving
        if target and state == "moving":
            target_text = f"→ ({target[0]:.1f}, {target[2]:.1f})"
            screen.blit(self.font.render(target_text, True, (100, 200, 255)), (panel_x + 120, panel_y + 68))
        
        completed = task_status.get("tasks_completed", 0)
        screen.blit(self.font.render(f"Completed: {completed}", True, (150, 150, 150)), (panel_x + 10, panel_y + 88))
    
    def _draw_event_log(self, screen, event_log):
        """Draw event log panel."""
        if not event_log:
            return
        
        entries = event_log[-5:]
        log_width = 260
        log_height = len(entries) * 18 + 10
        log_x = self.width - log_width - 10
        log_y = self.height - log_height - 35
        
        panel = pygame.Surface((log_width, log_height), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 150))
        screen.blit(panel, (log_x, log_y))
        
        for i, entry in enumerate(entries):
            screen.blit(self.font.render(entry[:34], True, (180, 180, 100)), (log_x + 5, log_y + 5 + i * 18))
    
    def _draw_controls(self, screen, is_humanoid):
        """Draw bottom control bar."""
        bar = pygame.Surface((self.width, 28), pygame.SRCALPHA)
        bar.fill((0, 0, 0, 180))
        screen.blit(bar, (0, self.height - 28))
        
        if is_humanoid:
            controls = "🤖 AUTONOMOUS | I/J/K/L: Override | D: Door | T: Task | TAB: Overlay | H: Help | ESC: Quit"
        else:
            controls = "I/J/K/L: Move | D: Door | TAB: Overlay | H: Help | ESC: Quit"
        screen.blit(self.font.render(controls, True, (150, 150, 150)), (10, self.height - 22))
    
    def _draw_help(self, screen):
        """Draw help overlay."""
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 220))
        screen.blit(overlay, (0, 0))
        
        help_lines = [
            ("VESPER Smart Home Simulation", self.title_font, (100, 200, 255)),
            ("", None, None),
            ("Movement (Manual Override):", self.font, (255, 255, 255)),
            ("  I/K - Walk forward/backward", self.font, (180, 180, 180)),
            ("  J/L - Turn left/right", self.font, (180, 180, 180)),
            ("", None, None),
            ("Smart Home Controls:", self.font, (255, 255, 255)),
            ("  D - Toggle nearest door", self.font, (180, 180, 180)),
            ("  T - Generate new task (LLM)", self.font, (180, 180, 180)),
            ("", None, None),
            ("View:", self.font, (255, 255, 255)),
            ("  TAB - Toggle HUD overlay", self.font, (180, 180, 180)),
            ("  N - Toggle navmesh visualization", self.font, (180, 180, 180)),
            ("", None, None),
            ("Autonomous Mode:", self.font, (255, 255, 255)),
            ("  Agent moves automatically to complete tasks", self.font, (180, 180, 180)),
            ("  Use I/J/K/L to temporarily override", self.font, (180, 180, 180)),
            ("", None, None),
            ("Press H to close", self.font, (150, 150, 150)),
        ]
        
        y = 60
        for text, font, color in help_lines:
            if font:
                screen.blit(font.render(text, True, color), (self.width // 2 - 150, y))
            y += 22
