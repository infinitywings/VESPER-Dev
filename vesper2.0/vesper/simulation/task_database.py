"""
Task Database for persistent storage of task history and analytics.

Uses SQLite for lightweight, file-based storage of:
- Completed tasks
- Daily schedules
- Activity patterns
- Time spent per room/activity
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from .task_system import DailySchedule, Task, TaskCategory, TaskStatus

logger = logging.getLogger(__name__)


class TaskDatabase:
    """
    SQLite-based storage for task history and analytics.
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the task database.
        
        Args:
            db_path: Path to SQLite database file (default: logs/vesper_tasks.db)
        """
        if db_path is None:
            # Use logs directory in project root
            project_root = Path(__file__).parent.parent.parent
            db_path = str(project_root / "logs" / "vesper_tasks.db")
        
        self.db_path = db_path
        
        # Ensure directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_database()
        
        logger.info(f"TaskDatabase initialized at {self.db_path}")
    
    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get database connection with context manager."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def _init_database(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT UNIQUE NOT NULL,
                    humanoid_id TEXT DEFAULT 'default',
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    room TEXT,
                    scheduled_time DATETIME,
                    started_at DATETIME,
                    completed_at DATETIME,
                    duration_seconds REAL,
                    actual_duration_seconds REAL,
                    progress REAL DEFAULT 0,
                    description TEXT,
                    metadata TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Daily schedules table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    humanoid_id TEXT DEFAULT 'default',
                    date DATE NOT NULL,
                    task_count INTEGER DEFAULT 0,
                    completed_count INTEGER DEFAULT 0,
                    schedule_json TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(humanoid_id, date)
                )
            """)
            
            # Room activity table (aggregated)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS room_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    humanoid_id TEXT DEFAULT 'default',
                    room TEXT NOT NULL,
                    date DATE NOT NULL,
                    visit_count INTEGER DEFAULT 0,
                    total_duration_seconds REAL DEFAULT 0,
                    UNIQUE(humanoid_id, room, date)
                )
            """)
            
            # Create indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_humanoid 
                ON tasks(humanoid_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_date 
                ON tasks(DATE(scheduled_time))
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_category 
                ON tasks(category)
            """)
    
    def save_task(self, task: Task, humanoid_id: str = "default"):
        """
        Save a task to the database.
        
        Args:
            task: Task to save
            humanoid_id: ID of the humanoid performing the task
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            actual_duration = None
            if task.started_at and task.completed_at:
                actual_duration = (task.completed_at - task.started_at).total_seconds()
            
            # Prepare metadata
            metadata = {
                "iot_triggers": task.iot_triggers,
                "actions": [
                    {"type": a.action_type, "target": a.target}
                    for a in task.actions
                ],
            }
            
            cursor.execute("""
                INSERT OR REPLACE INTO tasks (
                    task_id, humanoid_id, name, category, priority, status,
                    room, scheduled_time, started_at, completed_at,
                    duration_seconds, actual_duration_seconds, progress,
                    description, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.task_id,
                humanoid_id,
                task.name,
                task.category.value,
                task.priority.name,
                task.status.value,
                task.location.room_name if task.location else None,
                task.scheduled_time.isoformat() if task.scheduled_time else None,
                task.started_at.isoformat() if task.started_at else None,
                task.completed_at.isoformat() if task.completed_at else None,
                task.duration.total_seconds(),
                actual_duration,
                task.progress,
                task.description,
                json.dumps(metadata),
            ))
            
            # Update room activity if task has location and was completed
            if task.location and task.status == TaskStatus.COMPLETED and actual_duration:
                self._update_room_activity(
                    cursor, humanoid_id, task.location.room_name,
                    task.completed_at.date() if task.completed_at else datetime.now().date(),
                    actual_duration
                )
    
    def _update_room_activity(
        self,
        cursor: sqlite3.Cursor,
        humanoid_id: str,
        room: str,
        date: datetime,
        duration: float,
    ):
        """Update room activity aggregates."""
        cursor.execute("""
            INSERT INTO room_activity (humanoid_id, room, date, visit_count, total_duration_seconds)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(humanoid_id, room, date) DO UPDATE SET
                visit_count = visit_count + 1,
                total_duration_seconds = total_duration_seconds + ?
        """, (humanoid_id, room, date, duration, duration))
    
    def save_schedule(self, schedule: DailySchedule, humanoid_id: str = "default"):
        """Save a daily schedule."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            completed_count = sum(
                1 for t in schedule.tasks if t.status == TaskStatus.COMPLETED
            )
            
            schedule_json = json.dumps([t.to_dict() for t in schedule.tasks])
            
            cursor.execute("""
                INSERT OR REPLACE INTO daily_schedules (
                    humanoid_id, date, task_count, completed_count, schedule_json
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                humanoid_id,
                schedule.date.date(),
                len(schedule.tasks),
                completed_count,
                schedule_json,
            ))
        
        # Save individual tasks (outside the schedule transaction)
        for task in schedule.tasks:
            self.save_task(task, humanoid_id)
    
    def get_tasks_by_date(
        self,
        date: datetime,
        humanoid_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """Get all tasks for a specific date."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM tasks
                WHERE humanoid_id = ? AND DATE(scheduled_time) = ?
                ORDER BY scheduled_time
            """, (humanoid_id, date.date()))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_recent_tasks(
        self,
        limit: int = 10,
        humanoid_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """Get most recent tasks."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM tasks
                WHERE humanoid_id = ?
                ORDER BY completed_at DESC
                LIMIT ?
            """, (humanoid_id, limit))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_category_stats(
        self,
        days: int = 7,
        humanoid_id: str = "default",
    ) -> Dict[str, Dict[str, Any]]:
        """Get statistics by task category for recent days."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cutoff = datetime.now() - timedelta(days=days)
            
            cursor.execute("""
                SELECT 
                    category,
                    COUNT(*) as count,
                    SUM(actual_duration_seconds) as total_duration,
                    AVG(actual_duration_seconds) as avg_duration
                FROM tasks
                WHERE humanoid_id = ? 
                    AND completed_at >= ?
                    AND status = 'completed'
                GROUP BY category
            """, (humanoid_id, cutoff.isoformat()))
            
            stats = {}
            for row in cursor.fetchall():
                stats[row["category"]] = {
                    "count": row["count"],
                    "total_duration_minutes": (row["total_duration"] or 0) / 60,
                    "avg_duration_minutes": (row["avg_duration"] or 0) / 60,
                }
            
            return stats
    
    def get_room_stats(
        self,
        days: int = 7,
        humanoid_id: str = "default",
    ) -> Dict[str, Dict[str, Any]]:
        """Get room activity statistics for recent days."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cutoff = datetime.now() - timedelta(days=days)
            
            cursor.execute("""
                SELECT 
                    room,
                    SUM(visit_count) as total_visits,
                    SUM(total_duration_seconds) as total_duration
                FROM room_activity
                WHERE humanoid_id = ? AND date >= ?
                GROUP BY room
                ORDER BY total_duration DESC
            """, (humanoid_id, cutoff.date()))
            
            stats = {}
            for row in cursor.fetchall():
                stats[row["room"]] = {
                    "visits": row["total_visits"],
                    "total_duration_minutes": row["total_duration"] / 60,
                }
            
            return stats
    
    def get_daily_summary(
        self,
        date: datetime,
        humanoid_id: str = "default",
    ) -> Dict[str, Any]:
        """Get summary for a specific day."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Get schedule info
            cursor.execute("""
                SELECT * FROM daily_schedules
                WHERE humanoid_id = ? AND date = ?
            """, (humanoid_id, date.date()))
            
            schedule_row = cursor.fetchone()
            
            # Get task details
            cursor.execute("""
                SELECT 
                    status,
                    COUNT(*) as count,
                    SUM(actual_duration_seconds) as total_duration
                FROM tasks
                WHERE humanoid_id = ? AND DATE(scheduled_time) = ?
                GROUP BY status
            """, (humanoid_id, date.date()))
            
            status_counts = {}
            total_duration = 0
            for row in cursor.fetchall():
                status_counts[row["status"]] = row["count"]
                if row["total_duration"]:
                    total_duration += row["total_duration"]
            
            return {
                "date": date.date().isoformat(),
                "task_count": schedule_row["task_count"] if schedule_row else 0,
                "status_breakdown": status_counts,
                "total_active_minutes": total_duration / 60,
                "completion_rate": (
                    status_counts.get("completed", 0) / 
                    max(sum(status_counts.values()), 1)
                ),
            }
    
    def cleanup_old_data(self, days_to_keep: int = 30):
        """Remove data older than specified days."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cutoff = datetime.now() - timedelta(days=days_to_keep)
            
            cursor.execute("""
                DELETE FROM tasks
                WHERE scheduled_time < ?
            """, (cutoff.isoformat(),))
            
            cursor.execute("""
                DELETE FROM daily_schedules
                WHERE date < ?
            """, (cutoff.date(),))
            
            cursor.execute("""
                DELETE FROM room_activity
                WHERE date < ?
            """, (cutoff.date(),))
            
            # Vacuum to reclaim space
            cursor.execute("VACUUM")
            
            logger.info(f"Cleaned up data older than {cutoff.date()}")
    
    def export_to_json(self, output_path: str, days: int = 7):
        """Export recent data to JSON file."""
        cutoff = datetime.now() - timedelta(days=days)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Get tasks
            cursor.execute("""
                SELECT * FROM tasks
                WHERE scheduled_time >= ?
                ORDER BY scheduled_time
            """, (cutoff.isoformat(),))
            tasks = [dict(row) for row in cursor.fetchall()]
            
            # Get schedules
            cursor.execute("""
                SELECT * FROM daily_schedules
                WHERE date >= ?
            """, (cutoff.date(),))
            schedules = [dict(row) for row in cursor.fetchall()]
            
            # Get room activity
            cursor.execute("""
                SELECT * FROM room_activity
                WHERE date >= ?
            """, (cutoff.date(),))
            room_activity = [dict(row) for row in cursor.fetchall()]
        
        data = {
            "exported_at": datetime.now().isoformat(),
            "days_included": days,
            "tasks": tasks,
            "schedules": schedules,
            "room_activity": room_activity,
        }
        
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        
        logger.info(f"Exported {len(tasks)} tasks to {output_path}")
