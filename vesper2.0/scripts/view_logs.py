#!/usr/bin/env python3
"""
Log Viewer and Analyzer for VESPER ObjectNav

View and analyze simulation logs stored in the logs folder.
"""

import os
import sys
from pathlib import Path
from datetime import datetime
import re

PROJECT_ROOT = Path(__file__).parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"


def list_logs():
    """List all log files sorted by date."""
    if not LOGS_DIR.exists():
        print(f"No logs directory found at: {LOGS_DIR}")
        return []
    
    log_files = sorted(
        LOGS_DIR.glob("vesper_objectnav_*.log"),
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )
    
    if not log_files:
        print("No log files found.")
        return []
    
    print("=" * 80)
    print("VESPER ObjectNav Log Files")
    print("=" * 80)
    
    for idx, log_file in enumerate(log_files, 1):
        size = log_file.stat().st_size
        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        print(f"{idx:2d}. {log_file.name}")
        print(f"    Size: {size:,} bytes | Modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("=" * 80)
    return log_files


def analyze_log(log_file: Path):
    """Analyze a log file for errors and statistics."""
    print(f"\n{'=' * 80}")
    print(f"Analyzing: {log_file.name}")
    print(f"{'=' * 80}\n")
    
    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        return
    
    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Statistics
    total_lines = len(lines)
    errors = []
    warnings = []
    sensors_detected = []
    goals_reached = []
    automations = []
    bridge_stats = {}
    
    for i, line in enumerate(lines, 1):
        # Extract errors
        if '[ERROR]' in line or 'ERROR' in line or 'Traceback' in line:
            errors.append((i, line.strip()))
        
        # Extract warnings
        if '[WARNING]' in line or 'Warning:' in line:
            warnings.append((i, line.strip()))
        
        # Sensor detections
        if '🔴 Motion detected' in line:
            match = re.search(r'Motion detected in (.*?)!', line)
            if match:
                sensors_detected.append(match.group(1))
        
        # Goals reached
        if 'Goal reached!' in line:
            goals_reached.append(i)
        
        # Automations
        if '⚡ Automation:' in line:
            match = re.search(r'Automation: (.*)', line)
            if match:
                automations.append(match.group(1).strip())
        
        # Bridge stats
        if 'Firmware sensors active:' in line:
            match = re.search(r'Firmware sensors active: (\d+)', line)
            if match:
                bridge_stats['firmware_sensors'] = int(match.group(1))
        
        if 'Rooms with environmental sensors:' in line:
            match = re.search(r'Rooms with environmental sensors: (\d+)', line)
            if match:
                bridge_stats['rooms_with_sensors'] = int(match.group(1))
    
    # Print summary
    print("📊 Log Summary:")
    print(f"   Total lines: {total_lines:,}")
    print(f"   Errors: {len(errors)}")
    print(f"   Warnings: {len(warnings)}")
    print()
    
    if bridge_stats:
        print("🔧 Sensor Bridge:")
        print(f"   Firmware sensors active: {bridge_stats.get('firmware_sensors', 'N/A')}")
        print(f"   Rooms with sensors: {bridge_stats.get('rooms_with_sensors', 'N/A')}")
        print()
    
    print("📡 Sensor Activity:")
    print(f"   Motion detections: {len(sensors_detected)}")
    if sensors_detected:
        from collections import Counter
        room_counts = Counter(sensors_detected)
        print("   Top 5 rooms with motion:")
        for room, count in room_counts.most_common(5):
            print(f"      - {room}: {count} detections")
    print()
    
    print("🎯 Navigation:")
    print(f"   Goals reached: {len(goals_reached)}")
    print()
    
    print("⚡ Automations:")
    print(f"   Total automations triggered: {len(automations)}")
    if automations:
        from collections import Counter
        auto_counts = Counter(automations)
        print("   Top automations:")
        for auto, count in auto_counts.most_common(5):
            print(f"      - {auto[:60]}... ({count}x)")
    print()
    
    # Show errors if any
    if errors:
        print("❌ ERRORS FOUND:")
        print("=" * 80)
        for line_num, error in errors[:10]:  # Show first 10 errors
            print(f"Line {line_num}: {error}")
            if len(error) > 200:
                print("   [truncated]")
        if len(errors) > 10:
            print(f"\n... and {len(errors) - 10} more errors")
        print("=" * 80)
    else:
        print("✅ No errors found!")
    
    # Show warnings if any
    if warnings and len(warnings) <= 5:
        print("\n⚠️  Warnings:")
        for line_num, warning in warnings:
            print(f"Line {line_num}: {warning[:150]}")
    elif len(warnings) > 5:
        print(f"\n⚠️  {len(warnings)} warnings found (first 3 shown):")
        for line_num, warning in warnings[:3]:
            print(f"Line {line_num}: {warning[:150]}")
    
    print("\n" + "=" * 80)


def view_log(log_file: Path, tail: int = None, grep: str = None):
    """View log file contents."""
    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        return
    
    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    if grep:
        lines = [line for line in lines if grep.lower() in line.lower()]
        print(f"Showing lines matching '{grep}':")
    
    if tail:
        lines = lines[-tail:]
        print(f"Showing last {tail} lines:")
    
    print("=" * 80)
    for line in lines:
        print(line.rstrip())
    print("=" * 80)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="View and analyze VESPER ObjectNav logs"
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all log files"
    )
    parser.add_argument(
        "--analyze", "-a",
        type=str,
        metavar="LOG",
        help="Analyze a log file (use number from --list or filename)"
    )
    parser.add_argument(
        "--view", "-v",
        type=str,
        metavar="LOG",
        help="View a log file (use number from --list or filename)"
    )
    parser.add_argument(
        "--tail", "-t",
        type=int,
        metavar="N",
        help="Show only last N lines"
    )
    parser.add_argument(
        "--grep", "-g",
        type=str,
        metavar="PATTERN",
        help="Filter lines containing pattern"
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Analyze the latest log file"
    )
    parser.add_argument(
        "--errors-only",
        action="store_true",
        help="Show only errors from latest log"
    )
    
    args = parser.parse_args()
    
    # Default: list logs
    if not any([args.analyze, args.view, args.latest, args.errors_only]):
        args.list = True
    
    log_files = list_logs() if args.list or args.latest or args.errors_only else []
    
    if args.latest and log_files:
        analyze_log(log_files[0])
        return
    
    if args.errors_only and log_files:
        view_log(log_files[0], grep="ERROR")
        return
    
    if args.analyze:
        # Check if it's a number (index) or filename
        if args.analyze.isdigit():
            idx = int(args.analyze) - 1
            if 0 <= idx < len(log_files):
                analyze_log(log_files[idx])
            else:
                print(f"Invalid index: {args.analyze}")
        else:
            log_path = LOGS_DIR / args.analyze
            analyze_log(log_path)
    
    if args.view:
        # Check if it's a number (index) or filename
        if args.view.isdigit():
            idx = int(args.view) - 1
            if 0 <= idx < len(log_files):
                view_log(log_files[idx], tail=args.tail, grep=args.grep)
            else:
                print(f"Invalid index: {args.view}")
        else:
            log_path = LOGS_DIR / args.view
            view_log(log_path, tail=args.tail, grep=args.grep)


if __name__ == "__main__":
    main()
