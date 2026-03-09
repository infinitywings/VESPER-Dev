"""
Tests for pcap capture infrastructure.

Tests WiresharkLiveCapture, verify_pcap_capture helpers, and
pcap_attack_capture data structures.

NOTE: Tests that require tshark/dumpcap are skipped if not installed.
      Tests that require QEMU are skipped if qemu-system-arm is not installed.
"""

import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vesper.network.home_network import (
    NetworkConfig,
    NetworkMode,
    WiresharkConfig,
    WiresharkLiveCapture,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────

HAS_TSHARK = shutil.which("tshark") is not None
HAS_DUMPCAP = shutil.which("dumpcap") is not None
HAS_CAPTURE_TOOL = HAS_TSHARK or HAS_DUMPCAP
HAS_QEMU = shutil.which("qemu-system-arm") is not None

requires_capture_tool = pytest.mark.skipif(
    not HAS_CAPTURE_TOOL,
    reason="tshark or dumpcap not installed"
)

requires_qemu = pytest.mark.skipif(
    not HAS_QEMU,
    reason="qemu-system-arm not installed"
)


@pytest.fixture
def tmp_pcap_dir():
    """Create a temporary directory for pcap output."""
    d = tempfile.mkdtemp(prefix="vesper_test_pcap_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def echo_server():
    """Start a simple TCP echo server for traffic generation."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    running = True

    def serve():
        srv.settimeout(1.0)
        while running:
            try:
                conn, _ = srv.accept()
                data = conn.recv(4096)
                if data:
                    conn.sendall(data)
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    yield port

    running = False
    srv.close()
    t.join(timeout=3)


def loopback_iface():
    return "lo0" if platform.system() == "Darwin" else "lo"


# ─── Unit tests: WiresharkLiveCapture ─────────────────────────────────────────

class TestWiresharkLiveCapture:
    """Tests for WiresharkLiveCapture class."""

    def test_find_capture_tool(self):
        """Tool detection should find tshark or dumpcap if installed."""
        ws_config = WiresharkConfig(enabled=True)
        net_config = NetworkConfig(wireshark=ws_config)
        cap = WiresharkLiveCapture(net_config)

        if HAS_CAPTURE_TOOL:
            assert cap._tool is not None
            assert "tshark" in cap._tool or "dumpcap" in cap._tool
        else:
            # OK if not installed — just verifies no crash
            assert cap._tool is None

    def test_resolve_interface_bridge(self):
        """Bridge mode should resolve to docker0 (or equivalent)."""
        ws_config = WiresharkConfig(enabled=True)
        net_config = NetworkConfig(mode=NetworkMode.BRIDGE, wireshark=ws_config)
        cap = WiresharkLiveCapture(net_config)
        iface = cap._resolve_interface()
        assert iface == "docker0"

    def test_resolve_interface_macvlan(self):
        """Macvlan mode should resolve to parent interface."""
        ws_config = WiresharkConfig(enabled=True)
        net_config = NetworkConfig(
            mode=NetworkMode.MACVLAN,
            parent_interface="en0",
            wireshark=ws_config,
        )
        cap = WiresharkLiveCapture(net_config)
        iface = cap._resolve_interface()
        assert iface == "en0"

    def test_resolve_interface_host(self):
        """Host mode should resolve to loopback."""
        ws_config = WiresharkConfig(enabled=True)
        net_config = NetworkConfig(mode=NetworkMode.HOST, wireshark=ws_config)
        cap = WiresharkLiveCapture(net_config)
        iface = cap._resolve_interface()
        assert iface == "lo0"

    def test_resolve_interface_explicit_override(self):
        """Explicit capture_interface should override auto-detection."""
        ws_config = WiresharkConfig(enabled=True, capture_interface="eth99")
        net_config = NetworkConfig(wireshark=ws_config)
        cap = WiresharkLiveCapture(net_config)
        iface = cap._resolve_interface()
        assert iface == "eth99"

    def test_stop_without_start_returns_none(self):
        """Stopping without starting should not crash."""
        ws_config = WiresharkConfig(enabled=True)
        net_config = NetworkConfig(wireshark=ws_config)
        cap = WiresharkLiveCapture(net_config)
        result = cap.stop()
        assert result is None

    @requires_capture_tool
    def test_start_stop_creates_pcap(self, tmp_pcap_dir, echo_server):
        """Full capture cycle should create a .pcap file."""
        port = echo_server
        ws_config = WiresharkConfig(
            enabled=True,
            capture_interface=loopback_iface(),
            capture_filter=f"tcp port {port}",
            pcap_dir=tmp_pcap_dir,
        )
        net_config = NetworkConfig(mode=NetworkMode.HOST, wireshark=ws_config)
        cap = WiresharkLiveCapture(net_config)

        cap.start()
        assert cap._process is not None
        time.sleep(0.5)

        # Send some traffic
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect(("127.0.0.1", port))
            sock.sendall(b"HELLO VESPER TEST\n")
            sock.recv(4096)
            sock.close()
        except Exception:
            pass

        time.sleep(0.5)
        pcap_path = cap.stop()

        assert pcap_path is not None
        assert Path(pcap_path).exists()
        assert Path(pcap_path).stat().st_size > 0

    @requires_capture_tool
    def test_bpf_filter_applied(self, tmp_pcap_dir, echo_server):
        """BPF filter should limit captured traffic."""
        port = echo_server
        # Capture on a different port — should get 0 relevant packets
        ws_config = WiresharkConfig(
            enabled=True,
            capture_interface=loopback_iface(),
            capture_filter=f"tcp port {port + 9999}",
            pcap_dir=tmp_pcap_dir,
        )
        net_config = NetworkConfig(mode=NetworkMode.HOST, wireshark=ws_config)
        cap = WiresharkLiveCapture(net_config)

        cap.start()
        time.sleep(0.5)

        # Send traffic to real port — BPF should NOT capture it
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(("127.0.0.1", port))
            sock.sendall(b"BPF TEST\n")
            sock.recv(4096)
            sock.close()
        except Exception:
            pass

        time.sleep(0.5)
        pcap_path = cap.stop()

        # File should exist but have minimal content (just pcap header, no data packets)
        assert pcap_path is not None
        assert Path(pcap_path).exists()
        # pcap header is 24 bytes — no captured packets means file is small
        size = Path(pcap_path).stat().st_size
        assert size <= 200  # Just header, no real packets


# ─── Unit tests: CaptureRecord ────────────────────────────────────────────────

class TestCaptureRecord:
    """Tests for the CaptureRecord dataclass from pcap_attack_capture."""

    def test_capture_record_creation(self):
        """CaptureRecord should be constructible with all fields."""
        # Import from the script
        import sys
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from pcap_attack_capture import CaptureRecord

        record = CaptureRecord(
            pcap_path="/tmp/test.pcap",
            attack_name="test_attack",
            attack_category="buffer_overflow",
            attack_suite="firmware",
            device_type="smart_light",
            success=True,
            severity="high",
            duration_ms=42.0,
            pcap_size_bytes=1024,
            packet_count=10,
            timestamp="2025-01-01T00:00:00",
            cve_reference="CVE-2024-1234",
            label=1,
        )

        assert record.attack_name == "test_attack"
        assert record.label == 1
        assert record.pcap_size_bytes == 1024

    def test_benign_record_label(self):
        """Benign records should have label=0."""
        import sys
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from pcap_attack_capture import CaptureRecord

        record = CaptureRecord(
            pcap_path="/tmp/benign.pcap",
            attack_name="baseline_benign",
            attack_category="benign",
            attack_suite="benign",
            device_type="smart_light",
            success=True,
            label=0,
        )

        assert record.label == 0
        assert record.attack_suite == "benign"


# ─── Unit tests: verify_pcap_capture helpers ──────────────────────────────────

class TestVerifyHelpers:
    """Tests for helper functions in verify_pcap_capture."""

    def test_resolve_loopback_interface(self):
        """Should return lo0 on macOS, lo on Linux."""
        import sys
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from verify_pcap_capture import resolve_loopback_interface

        iface = resolve_loopback_interface()
        if platform.system() == "Darwin":
            assert iface == "lo0"
        else:
            assert iface == "lo"

    def test_check_capture_tool(self):
        """Should find tshark/dumpcap or return empty string."""
        import sys
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from verify_pcap_capture import check_capture_tool

        tool = check_capture_tool()
        if HAS_CAPTURE_TOOL:
            assert len(tool) > 0
        else:
            assert tool == ""

    def test_mini_echo_server(self, echo_server):
        """Echo server should echo back data."""
        port = echo_server
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(("127.0.0.1", port))
        sock.sendall(b"HELLO\n")
        data = sock.recv(4096)
        sock.close()
        assert b"HELLO" in data


# ─── Integration tests: manifest generation ──────────────────────────────────

class TestManifest:
    """Tests for manifest.json generation."""

    def test_write_manifest(self, tmp_pcap_dir):
        """Manifest should be valid JSON with correct structure."""
        import sys
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from pcap_attack_capture import CaptureRecord, write_manifest

        records = [
            CaptureRecord(
                pcap_path=f"{tmp_pcap_dir}/test1.pcap",
                attack_name="attack_1",
                attack_category="buffer_overflow",
                attack_suite="firmware",
                device_type="smart_light",
                success=True,
                severity="high",
                pcap_size_bytes=500,
                packet_count=5,
                label=1,
            ),
            CaptureRecord(
                pcap_path=f"{tmp_pcap_dir}/benign.pcap",
                attack_name="baseline_benign",
                attack_category="benign",
                attack_suite="benign",
                device_type="smart_light",
                success=True,
                pcap_size_bytes=200,
                packet_count=3,
                label=0,
            ),
        ]

        manifest_path = write_manifest(records, tmp_pcap_dir)
        assert Path(manifest_path).exists()

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["total_captures"] == 2
        assert manifest["firmware_captures"] == 1
        assert manifest["benign_captures"] == 1
        assert manifest["total_pcap_bytes"] == 700
        assert manifest["total_packets"] == 8
        assert len(manifest["captures"]) == 2
        assert manifest["captures"][0]["label"] == 1
        assert manifest["captures"][1]["label"] == 0


# ─── Integration test: full capture round-trip ────────────────────────────────

@requires_capture_tool
class TestCaptureRoundTrip:
    """End-to-end capture test using the verify script's logic."""

    def test_capture_with_echo_server(self, tmp_pcap_dir, echo_server):
        """Full round-trip: start capture → send traffic → stop → validate."""
        import sys
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from verify_pcap_capture import verify_capture_round_trip

        results = verify_capture_round_trip(
            interface=loopback_iface(),
            port=echo_server,
            pcap_dir=tmp_pcap_dir,
            num_messages=5,
            use_firmware=False,
        )

        assert results["capture_started"] is True
        assert results["messages_sent"] == 5
        assert results["messages_echoed"] > 0
        assert results["pcap_exists"] is True
        assert results["pcap_size_bytes"] > 0
        assert results["pcap_packet_count"] > 0
