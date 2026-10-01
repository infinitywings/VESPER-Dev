#!/usr/bin/env python3
"""
VESPER wmediumd Integration Helper

Manages the wmediumd wireless medium daemon for realistic 802.11
channel modeling with mac80211_hwsim. Provides:
  - Path-loss model (log-distance with configurable exponent)
  - Per-link SNR model
  - Automatic MAC address discovery from hwsim interfaces
  - Config file generation and process management

wmediumd intercepts frames between mac80211_hwsim radios and applies:
  - Signal-to-noise ratio per link
  - Rate-dependent packet error probability
  - Propagation delay
  - Retransmissions (triggered by frame loss)

This gives VESPER realistic WiFi behavior that the default hwsim
in-kernel forwarding mode cannot provide.

Usage:
    from wmediumd_helper import WmediumdManager
    wm = WmediumdManager(output_dir="/tmp/vesper-wmediumd")
    wm.start(mac_addresses=["02:00:00:00:00:00", "02:00:00:00:01:00", ...])
    # ... run experiments ...
    wm.stop()
"""

from __future__ import annotations
import json
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vesper.wmediumd")


@dataclass
class WmediumdConfig:
    """Configuration for wmediumd channel model."""

    # Model type: "path_loss", "snr", "prob", "perfect"
    model_type: str = "path_loss"

    # Path-loss model parameters (used when model_type == "path_loss")
    # Typical indoor values: path_loss_exp=3.0-4.0, xg=3-6 dB
    path_loss_exp: float = 3.5       # Path-loss exponent (indoor residential)
    xg: float = 3.0                  # Shadow fading std dev (dB)
    tx_power: float = 15.0           # Transmit power (dBm) per node

    # Node positions in meters (x, y) — models a typical home layout
    # Default: AP at center, two stations in different rooms, attacker outside
    positions: List[Tuple[float, float]] = field(default_factory=lambda: [
        (0.0, 0.0),       # phy0: AP (living room center)
        (5.0, 3.0),       # phy1: Station 0 (bedroom, ~5.8m from AP)
        (-4.0, 2.0),      # phy2: Station 1 (kitchen, ~4.5m from AP)
        (12.0, 0.0),      # phy3: Attacker radio (outside, ~12m)
    ])

    # Per-link SNR overrides (used when model_type == "snr")
    # Format: list of (node_i, node_j, snr_dB)
    snr_links: List[Tuple[int, int, float]] = field(default_factory=lambda: [
        (0, 1, 30),   # AP ↔ STA0: good link (same floor, ~6m)
        (0, 2, 25),   # AP ↔ STA1: decent link (~4.5m, through wall)
        (1, 2, 15),   # STA0 ↔ STA1: weak (different rooms)
        (0, 3, 10),   # AP ↔ attacker: weak (outside)
        (1, 3, 8),    # STA0 ↔ attacker: very weak
        (2, 3, 8),    # STA1 ↔ attacker: very weak
    ])

    # Per-link loss probabilities (used when model_type == "prob")
    # Format: list of (node_i, node_j, loss_probability)
    prob_links: List[Tuple[int, int, float]] = field(default_factory=lambda: [
        (0, 1, 0.02),   # AP ↔ STA0: 2% loss
        (0, 2, 0.05),   # AP ↔ STA1: 5% loss (through wall)
        (1, 2, 0.10),   # STA0 ↔ STA1: 10% loss
        (0, 3, 0.15),   # AP ↔ attacker: 15% loss
    ])


# Predefined scenarios for experiments
SCENARIOS = {
    "ideal": WmediumdConfig(
        model_type="path_loss",
        path_loss_exp=2.0,   # Free-space (near-ideal indoor)
        xg=0.0,
        tx_power=20.0,
        positions=[(0, 0), (3, 0), (0, 3), (10, 0)],
    ),
    "typical_home": WmediumdConfig(
        model_type="path_loss",
        path_loss_exp=3.5,   # Residential indoor with walls
        xg=3.0,
        tx_power=15.0,
        positions=[(0, 0), (5, 3), (-4, 2), (12, 0)],
    ),
    "challenging": WmediumdConfig(
        model_type="path_loss",
        path_loss_exp=4.5,   # Multi-wall, concrete
        xg=6.0,
        tx_power=15.0,
        positions=[(0, 0), (8, 5), (-6, 4), (15, 0)],
    ),
    "snr_realistic": WmediumdConfig(
        model_type="snr",
        snr_links=[
            (0, 1, 30),   # AP ↔ STA0: strong
            (0, 2, 22),   # AP ↔ STA1: moderate (through wall)
            (1, 2, 12),   # STA0 ↔ STA1: weak
            (0, 3, 8),    # AP ↔ attacker: marginal
            (1, 3, 5),    # STA0 ↔ attacker: very weak
            (2, 3, 5),    # STA1 ↔ attacker: very weak
        ],
    ),
}


class WmediumdManager:
    """Manages the wmediumd process lifecycle."""

    def __init__(
        self,
        output_dir: str = "/tmp/vesper-wmediumd",
        wmediumd_bin: str = "wmediumd",
        config: Optional[WmediumdConfig] = None,
        scenario: str = "typical_home",
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.wmediumd_bin = wmediumd_bin
        self.config = config or SCENARIOS.get(scenario, SCENARIOS["typical_home"])
        self._process: Optional[subprocess.Popen] = None
        self._config_path = self.output_dir / "wmediumd.cfg"
        self._log_path = self.output_dir / "wmediumd.log"

    @staticmethod
    def is_installed() -> bool:
        """Check if wmediumd binary is available."""
        try:
            result = subprocess.run(
                ["which", "wmediumd"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def get_hwsim_mac_addresses() -> List[str]:
        """
        Discover MAC addresses of all mac80211_hwsim interfaces.
        These are needed for the wmediumd config file.

        Returns MAC addresses in phy order (phy0, phy1, phy2, ...).
        """
        macs = []
        try:
            # Get all wlan interfaces sorted
            result = subprocess.run(
                ["bash", "-c", "ls -1 /sys/class/net/ | grep wlan | sort"],
                capture_output=True, text=True, timeout=5
            )
            ifaces = [x.strip() for x in result.stdout.strip().split("\n") if x.strip()]

            for iface in ifaces:
                # Read MAC from sysfs — check root namespace first
                mac_path = f"/sys/class/net/{iface}/address"
                if os.path.exists(mac_path):
                    with open(mac_path) as f:
                        mac = f.read().strip()
                        if mac and mac != "00:00:00:00:00:00":
                            macs.append(mac)

            # If interfaces were moved to namespaces, read from there
            if len(macs) < 2:
                for ns_name in ["ns-sta0", "ns-sta1"]:
                    try:
                        result = subprocess.run(
                            ["ip", "netns", "exec", ns_name, "bash", "-c",
                             "cat /sys/class/net/wlan*/address 2>/dev/null"],
                            capture_output=True, text=True, timeout=5
                        )
                        for line in result.stdout.strip().split("\n"):
                            mac = line.strip()
                            if mac and mac != "00:00:00:00:00:00" and mac not in macs:
                                macs.append(mac)
                    except Exception:
                        pass

        except Exception as e:
            logger.warning(f"Failed to discover hwsim MACs: {e}")

        logger.info(f"Discovered hwsim MACs: {macs}")
        return macs

    def generate_config(self, mac_addresses: Optional[List[str]] = None) -> str:
        """
        Generate wmediumd config file in libconfig format.

        Args:
            mac_addresses: List of MAC addresses for hwsim interfaces.
                          If None, auto-discovers from /sys/class/net/.

        Returns:
            Path to generated config file.
        """
        if mac_addresses is None:
            mac_addresses = self.get_hwsim_mac_addresses()

        if len(mac_addresses) < 2:
            raise RuntimeError(
                f"Need ≥2 MAC addresses for wmediumd, got {len(mac_addresses)}. "
                f"Is mac80211_hwsim loaded?"
            )

        cfg = self.config
        num_nodes = len(mac_addresses)

        lines = []
        lines.append("ifaces :")
        lines.append("{")
        lines.append("    ids = [")
        for i, mac in enumerate(mac_addresses):
            comma = "," if i < len(mac_addresses) - 1 else ""
            lines.append(f'        "{mac}"{comma}')
        lines.append("    ];")

        # Add per-link SNR if using SNR model
        if cfg.model_type == "snr":
            lines.append("")
            lines.append("    links = (")
            for i, (a, b, snr) in enumerate(cfg.snr_links):
                if a < num_nodes and b < num_nodes:
                    comma = "," if i < len(cfg.snr_links) - 1 else ""
                    lines.append(f"        ({a}, {b}, {int(snr)}){comma}")
            lines.append("    );")

        lines.append("};")

        # Add model section for path_loss and prob types
        if cfg.model_type == "path_loss":
            lines.append("")
            lines.append("model:")
            lines.append("{")
            lines.append('    type = "path_loss";')
            lines.append("    positions = (")
            for i, (x, y) in enumerate(cfg.positions[:num_nodes]):
                comma = "," if i < num_nodes - 1 else ""
                lines.append(f"        ({x:7.1f}, {y:7.1f}){comma}")
            lines.append("    );")
            lines.append(f"    tx_powers = ({', '.join([str(cfg.tx_power)] * num_nodes)});")
            lines.append("")
            lines.append(f'    model_name = "log_distance";')
            lines.append(f"    path_loss_exp = {cfg.path_loss_exp};")
            lines.append(f"    xg = {cfg.xg};")
            lines.append("};")

        elif cfg.model_type == "prob":
            lines.append("")
            lines.append("model:")
            lines.append("{")
            lines.append('    type = "prob";')
            lines.append("")
            lines.append("    default_prob = 1.0;")  # 100% loss by default
            lines.append("    links = (")
            for i, (a, b, prob) in enumerate(cfg.prob_links):
                if a < num_nodes and b < num_nodes:
                    comma = "," if i < len(cfg.prob_links) - 1 else ""
                    lines.append(f"        ({a}, {b}, {prob:.6f}){comma}")
            lines.append("    );")
            lines.append("};")

        config_text = "\n".join(lines) + "\n"

        with open(self._config_path, "w") as f:
            f.write(config_text)

        logger.info(f"Generated wmediumd config: {self._config_path}")
        logger.info(f"  Model: {cfg.model_type}, Nodes: {num_nodes}")
        if cfg.model_type == "path_loss":
            logger.info(f"  Path-loss exp: {cfg.path_loss_exp}, "
                        f"Shadow fading: {cfg.xg} dB, Tx power: {cfg.tx_power} dBm")

        return str(self._config_path)

    def start(self, mac_addresses: Optional[List[str]] = None) -> bool:
        """
        Start wmediumd with the configured channel model.

        IMPORTANT: Must be called AFTER `modprobe mac80211_hwsim radios=N`
        but BEFORE starting hostapd/wpa_supplicant.

        Args:
            mac_addresses: MAC addresses of hwsim interfaces.

        Returns:
            True if started successfully.
        """
        if self._process is not None:
            logger.warning("wmediumd already running, stopping first")
            self.stop()

        # Generate config
        config_path = self.generate_config(mac_addresses)

        # Start wmediumd
        cmd = [self.wmediumd_bin, "-c", config_path]
        logger.info(f"Starting wmediumd: {' '.join(cmd)}")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=open(self._log_path, "w"),
                stderr=subprocess.STDOUT,
            )
            time.sleep(1.0)  # Give it time to bind to netlink

            if self._process.poll() is not None:
                # Process already exited
                log_content = self._log_path.read_text() if self._log_path.exists() else "no log"
                logger.error(f"wmediumd exited immediately. Log:\n{log_content}")
                self._process = None
                return False

            logger.info(f"wmediumd started (PID {self._process.pid})")
            return True

        except FileNotFoundError:
            logger.error(
                "wmediumd binary not found. Install with:\n"
                "  git clone https://github.com/bcopeland/wmediumd\n"
                "  cd wmediumd && make && sudo make install"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to start wmediumd: {e}")
            return False

    def stop(self):
        """Stop the wmediumd process."""
        if self._process is not None:
            logger.info(f"Stopping wmediumd (PID {self._process.pid})")
            try:
                self._process.send_signal(signal.SIGTERM)
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception as e:
                logger.warning(f"Error stopping wmediumd: {e}")
            self._process = None

        # Also kill any orphaned wmediumd processes
        try:
            subprocess.run(
                ["killall", "wmediumd"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

    def is_running(self) -> bool:
        """Check if wmediumd is currently running."""
        if self._process is not None and self._process.poll() is None:
            return True
        # Check for any wmediumd process
        try:
            result = subprocess.run(
                ["pgrep", "-c", "wmediumd"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip() not in ("0", "")
        except Exception:
            return False

    def get_log(self) -> str:
        """Return wmediumd log content."""
        if self._log_path.exists():
            return self._log_path.read_text()
        return ""


def install_wmediumd(install_dir: str = "/tmp/wmediumd-build") -> bool:
    """
    Build and install wmediumd from source.

    Requires: git, gcc, make, libnl-3-dev, libnl-genl-3-dev, libconfig-dev
    """
    install_path = Path(install_dir)

    logger.info("Installing wmediumd from source...")

    try:
        # Install dependencies
        subprocess.run(
            ["sudo", "apt-get", "install", "-y",
             "libnl-3-dev", "libnl-genl-3-dev", "libconfig-dev",
             "git", "gcc", "make"],
            check=True, timeout=120,
        )

        # Clone and build
        if not (install_path / "wmediumd").exists():
            install_path.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "https://github.com/bcopeland/wmediumd",
                 str(install_path / "wmediumd")],
                check=True, timeout=60,
            )

        subprocess.run(
            ["make", "-C", str(install_path / "wmediumd")],
            check=True, timeout=60,
        )

        subprocess.run(
            ["sudo", "make", "-C", str(install_path / "wmediumd"), "install"],
            check=True, timeout=30,
        )

        logger.info("wmediumd installed successfully")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install wmediumd: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error installing wmediumd: {e}")
        return False


if __name__ == "__main__":
    """Quick test: generate a config file and show it."""
    logging.basicConfig(level=logging.INFO)

    print("=== VESPER wmediumd Helper ===\n")

    # Check installation
    if WmediumdManager.is_installed():
        print("✓ wmediumd is installed")
    else:
        print("✗ wmediumd is NOT installed")
        print("  Install with: git clone https://github.com/bcopeland/wmediumd && cd wmediumd && make && sudo make install")
        print("  Dependencies: sudo apt install libnl-3-dev libnl-genl-3-dev libconfig-dev")

    # Generate example configs for all scenarios
    for name, scenario in SCENARIOS.items():
        print(f"\n--- Scenario: {name} ---")
        wm = WmediumdManager(
            output_dir=f"/tmp/vesper-wmediumd-test/{name}",
            config=scenario,
        )
        # Use example MACs (real ones come from hwsim at runtime)
        example_macs = [
            "02:00:00:00:00:00",
            "02:00:00:00:01:00",
            "02:00:00:00:02:00",
            "02:00:00:00:03:00",
        ]
        path = wm.generate_config(mac_addresses=example_macs)
        print(f"  Config: {path}")
        with open(path) as f:
            print(f.read())
