#!/usr/bin/env python3
"""
Fitness Intelligence System - Evidence-based resistance training optimization.
Built for a 2x/week resistance + basketball + running schedule, targeting fat loss and body recomp.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from enum import Enum
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text


class ProgressionStatus(Enum):
    ON_TRACK = "On Track"
    PLATEAU = "Plateau"
    REGRESSION = "Regression"


class AlertPriority(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class SetData:
    set_number: int
    weight_lbs: float
    reps: int
    rpe: float = 7.0
    notes: str = ""


@dataclass
class ExerciseData:
    machine: str
    muscle_group: str
    sets: list[SetData] = field(default_factory=list)
    rest_seconds: int = 120


@dataclass
class WorkoutEntry:
    date: str
    session_type: str
    duration_minutes: int
    exercises: list[ExerciseData] = field(default_factory=list)
    overall_fatigue: int = 5
    notes: str = ""


@dataclass
class SorenessEntry:
    date: str
    muscle: str
    soreness_level: int  # 0-10
    notes: str = ""


@dataclass
class Alert:
    priority: AlertPriority
    alert_type: str
    message: str
    action: str


class FitnessDashboard:
    """Main Fitness Intelligence System."""

    def __init__(self):
        self.workouts: list[WorkoutEntry] = []
        self.soreness_data: list[SorenessEntry] = []
        self.console = Console()

    def load_from_excel(self, filepath: str) -> dict:
        """Parse workout history from Excel/CSV file."""
        try:
            if filepath.endswith('.csv'):
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath)

            # Expected columns: Date, Exercise, Weight, Reps, Sets, RPE, Notes
            required_cols = ['Date', 'Exercise', 'Weight', 'Reps']
            missing = [c for c in required_cols if c not in df.columns]
            if missing:
                return {"error": f"Missing required columns: {missing}"}

            # Group by date to create workout entries
            df['Date'] = pd.to_datetime(df['Date'])
            for date, group in df.groupby('Date'):
                workout = WorkoutEntry(
                    date=date.strftime('%Y-%m-%d'),
                    session_type=self._infer_session_type(group),
                    duration_minutes=60
                )
                for _, row in group.iterrows():
                    exercise = ExerciseData(
                        machine=row['Exercise'],
                        muscle_group=self._infer_muscle_group(row['Exercise'])
                    )
                    sets_count = int(row.get('Sets', 3))
                    for i in range(sets_count):
                        exercise.sets.append(SetData(
                            set_number=i + 1,
                            weight_lbs=float(row['Weight']),
                            reps=int(row['Reps']),
                            rpe=float(row.get('RPE', 7)),
                            notes=str(row.get('Notes', ''))
                        ))
                    workout.exercises.append(exercise)
                self.workouts.append(workout)

            return {
                "sessions": len(self.workouts),
                "date_range": f"{self.workouts[0].date} to {self.workouts[-1].date}" if self.workouts else "N/A"
            }
        except Exception as e:
            return {"error": str(e)}

    def add_workout(self, workout: WorkoutEntry):
        """Add a workout entry."""
        self.workouts.append(workout)

    def add_soreness(self, soreness: SorenessEntry):
        """Add a soreness entry."""
        self.soreness_data.append(soreness)

    # ==================== ANALYSIS MODULES ====================

    def calculate_e1rm(self, weight: float, reps: int) -> float:
        """Calculate estimated 1RM: weight × (1 + reps/30)."""
        return weight * (1 + reps / 30)

    def get_progressive_overload_status(self) -> dict[str, dict]:
        """
        Track progression status for each exercise.
        Returns status per exercise with e1RM trends.
        """
        exercise_history: dict[str, list[tuple[str, float]]] = {}

        for workout in self.workouts:
            for exercise in workout.exercises:
                if exercise.sets:
                    # Use best set (highest e1RM) from workout
                    best_e1rm = max(
                        self.calculate_e1rm(s.weight_lbs, s.reps)
                        for s in exercise.sets
                    )
                    if exercise.machine not in exercise_history:
                        exercise_history[exercise.machine] = []
                    exercise_history[exercise.machine].append((workout.date, best_e1rm))

        results = {}
        for exercise, history in exercise_history.items():
            if len(history) < 2:
                results[exercise] = {
                    "status": ProgressionStatus.ON_TRACK,
                    "current_e1rm": history[-1][1] if history else 0,
                    "peak_e1rm": history[-1][1] if history else 0,
                    "trend_pct": 0,
                    "sessions": len(history)
                }
                continue

            current_e1rm = history[-1][1]
            peak_e1rm = max(h[1] for h in history)
            recent_3 = [h[1] for h in history[-3:]]

            # Determine status
            if current_e1rm < peak_e1rm * 0.95:
                status = ProgressionStatus.REGRESSION
            elif len(recent_3) >= 3 and recent_3[-1] <= recent_3[0]:
                status = ProgressionStatus.PLATEAU
            else:
                status = ProgressionStatus.ON_TRACK

            # Calculate trend
            if len(history) >= 2:
                trend_pct = ((current_e1rm - history[0][1]) / history[0][1]) * 100
            else:
                trend_pct = 0

            results[exercise] = {
                "status": status,
                "current_e1rm": round(current_e1rm, 1),
                "peak_e1rm": round(peak_e1rm, 1),
                "trend_pct": round(trend_pct, 1),
                "sessions": len(history)
            }

        return results

    def calculate_volume_load(self, weeks: int = 1) -> dict[str, dict]:
        """
        Calculate weekly volume load per muscle group.
        Volume Load = sets × reps × weight
        """
        cutoff = datetime.now() - timedelta(weeks=weeks * 7)
        muscle_volume: dict[str, dict] = {}

        for workout in self.workouts:
            workout_date = datetime.strptime(workout.date, '%Y-%m-%d')
            if workout_date < cutoff:
                continue

            for exercise in workout.exercises:
                muscle = exercise.muscle_group
                if muscle not in muscle_volume:
                    muscle_volume[muscle] = {"sets": 0, "volume_load": 0, "last_trained": workout.date}

                for s in exercise.sets:
                    muscle_volume[muscle]["sets"] += 1
                    muscle_volume[muscle]["volume_load"] += s.weight_lbs * s.reps

                muscle_volume[muscle]["last_trained"] = max(
                    muscle_volume[muscle]["last_trained"], workout.date
                )

        # Add volume status
        for muscle, data in muscle_volume.items():
            sets = data["sets"]
            if sets < 6:
                data["status"] = "Below MEV"
            elif sets <= 10:
                data["status"] = "Minimum Effective"
            elif sets <= 15:
                data["status"] = "Optimal"
            elif sets <= 20:
                data["status"] = "High Volume"
            else:
                data["status"] = "Above MRV"

        return muscle_volume

    def get_readiness_score(self, muscle_group: str) -> dict:
        """
        Calculate readiness score for a muscle group.
        readiness_score = 10 - soreness_level - recovery_debt
        """
        # Get latest soreness
        muscle_soreness = [s for s in self.soreness_data if s.muscle == muscle_group]
        soreness_level = muscle_soreness[-1].soreness_level if muscle_soreness else 0

        # Calculate recovery debt
        volume_data = self.calculate_volume_load(weeks=1)
        last_trained = volume_data.get(muscle_group, {}).get("last_trained")

        recovery_debt = 0
        if last_trained:
            days_since = (datetime.now() - datetime.strptime(last_trained, '%Y-%m-%d')).days
            if days_since < 2:
                recovery_debt = 2

        readiness = 10 - soreness_level - recovery_debt

        if readiness < 5:
            recommendation = "Skip or reduced volume"
        elif readiness <= 7:
            recommendation = "Proceed with caution, -10% intensity"
        else:
            recommendation = "Full training capacity"

        return {
            "score": readiness,
            "soreness": soreness_level,
            "recovery_debt": recovery_debt,
            "recommendation": recommendation
        }

    def generate_next_workout(self) -> dict:
        """
        Generate optimal workout prescription using Chain-of-Thought logic.
        """
        # Step 1: Get muscle group readiness
        volume_data = self.calculate_volume_load(weeks=1)
        muscle_groups = list(volume_data.keys()) or ["chest", "back", "quads", "shoulders"]

        readiness_scores = {}
        for muscle in muscle_groups:
            readiness_scores[muscle] = self.get_readiness_score(muscle)

        # Filter out low readiness
        available_muscles = [
            m for m, r in readiness_scores.items() if r["score"] >= 5
        ]

        # Step 2: Get progression status
        progression = self.get_progressive_overload_status()

        # Step 3: Build workout
        exercises = []
        exercise_map = {
            "chest": ("Bench Press", True),
            "back": ("Lat Pulldown", True),
            "quads": ("Leg Press", True),
            "shoulders": ("Overhead Press", False),
            "hamstrings": ("Romanian Deadlift", True),
            "biceps": ("Bicep Curl", False),
            "triceps": ("Tricep Pushdown", False),
        }

        for muscle in available_muscles[:4]:
            if muscle not in exercise_map:
                continue

            exercise_name, is_compound = exercise_map[muscle]
            ex_progression = progression.get(exercise_name, {})
            status = ex_progression.get("status", ProgressionStatus.ON_TRACK)
            current_e1rm = ex_progression.get("current_e1rm", 100)

            # Calculate target based on status
            if status == ProgressionStatus.ON_TRACK:
                target_weight = round(current_e1rm * 0.75 + 5, 0)
                rationale = "+5 lbs from last session"
                sets = 3
            elif status == ProgressionStatus.PLATEAU:
                target_weight = round(current_e1rm * 0.75, 0)
                rationale = "Adding 1 set to break plateau"
                sets = 4
            else:
                target_weight = round(current_e1rm * 0.65, 0)
                rationale = "Reduced weight 10%, focus form"
                sets = 3

            exercises.append({
                "exercise": exercise_name,
                "muscle": muscle,
                "is_compound": is_compound,
                "target_weight": target_weight,
                "target_reps": 8 if is_compound else 10,
                "target_sets": sets,
                "rationale": rationale,
                "rest_minutes": "2-3" if is_compound else "1-2"
            })

        # Sort: compounds first
        exercises.sort(key=lambda x: (not x["is_compound"], x["muscle"]))

        # Muscles to avoid
        avoid_muscles = [
            {"muscle": m, "reason": f"Readiness {r['score']}/10"}
            for m, r in readiness_scores.items() if r["score"] < 5
        ]

        return {
            "focus": self._determine_focus(available_muscles),
            "estimated_duration": "45-60 min",
            "exercises": exercises,
            "muscles_to_avoid": avoid_muscles
        }

    def generate_alerts(self) -> list[Alert]:
        """Generate alerts based on current data."""
        alerts = []
        progression = self.get_progressive_overload_status()
        volume = self.calculate_volume_load(weeks=1)

        # Check for plateaus
        for exercise, data in progression.items():
            if data["status"] == ProgressionStatus.PLATEAU:
                alerts.append(Alert(
                    priority=AlertPriority.HIGH,
                    alert_type="Plateau Detected",
                    message=f"{exercise}: No progression in recent sessions",
                    action="Consider deload week or exercise variation"
                ))
            elif data["status"] == ProgressionStatus.REGRESSION:
                alerts.append(Alert(
                    priority=AlertPriority.HIGH,
                    alert_type="Regression",
                    message=f"{exercise}: e1RM down {abs(data['trend_pct']):.1f}% from peak",
                    action="Check form, sleep, nutrition. Consider deload."
                ))

        # Check volume
        for muscle, data in volume.items():
            if data["status"] == "Above MRV":
                alerts.append(Alert(
                    priority=AlertPriority.HIGH,
                    alert_type="Overtraining Risk",
                    message=f"{muscle}: {data['sets']} sets exceeds MRV",
                    action="Reduce volume or take rest day"
                ))

        # Check for PRs
        for exercise, data in progression.items():
            if data["current_e1rm"] >= data["peak_e1rm"] and data["sessions"] > 1:
                alerts.append(Alert(
                    priority=AlertPriority.LOW,
                    alert_type="PR Achieved",
                    message=f"{exercise}: New e1RM of {data['current_e1rm']} lbs",
                    action="Log and celebrate!"
                ))

        return alerts

    # ==================== DASHBOARD DISPLAY ====================

    def display_dashboard(self):
        """Display the full KPI dashboard."""
        self.console.clear()
        self.console.print(Panel.fit(
            "[bold blue]FITNESS INTELLIGENCE DASHBOARD[/bold blue]",
            subtitle="Evidence-based training optimization"
        ))

        # Tier 1: Headline Metrics
        self._display_headline_metrics()

        # Tier 2: Muscle Group Breakdown
        self._display_muscle_breakdown()

        # Tier 3: Exercise Tracking
        self._display_exercise_tracking()

        # Alerts
        self._display_alerts()

        # Next Workout
        self._display_next_workout()

    def _display_headline_metrics(self):
        """Display Tier 1 headline KPIs."""
        volume = self.calculate_volume_load(weeks=1)
        progression = self.get_progressive_overload_status()

        total_sets = sum(d["sets"] for d in volume.values())
        improving = sum(1 for d in progression.values() if d["status"] == ProgressionStatus.ON_TRACK)
        total_exercises = len(progression)

        # Calculate average readiness
        readiness_scores = [self.get_readiness_score(m)["score"] for m in volume.keys()]
        avg_readiness = sum(readiness_scores) / len(readiness_scores) if readiness_scores else 7

        # Session consistency (simplified)
        consistency = len(self.workouts) / max(1, len(self.workouts)) * 100

        cards = [
            Panel(f"[bold]{total_sets}[/bold]\n[dim]Weekly Sets[/dim]", title="VOLUME"),
            Panel(f"[bold]{improving}/{total_exercises}[/bold]\n[dim]Progressing[/dim]", title="PROGRESSION"),
            Panel(f"[bold]{avg_readiness:.1f}/10[/bold]\n[dim]Avg Score[/dim]", title="RECOVERY"),
            Panel(f"[bold]{len(self.workouts)}[/bold]\n[dim]Sessions[/dim]", title="CONSISTENCY"),
        ]

        self.console.print(Columns(cards, equal=True, expand=True))

    def _display_muscle_breakdown(self):
        """Display Tier 2 muscle group breakdown."""
        self.console.print("\n[bold]MUSCLE GROUP BREAKDOWN[/bold]")

        table = Table(show_header=True, header_style="bold")
        table.add_column("Muscle Group")
        table.add_column("Weekly Sets", justify="right")
        table.add_column("Volume Status")
        table.add_column("Last Trained")
        table.add_column("Readiness", justify="right")

        volume = self.calculate_volume_load(weeks=1)
        for muscle, data in volume.items():
            readiness = self.get_readiness_score(muscle)
            status_color = "green" if "Optimal" in data["status"] else "yellow" if "Minimum" in data["status"] else "red"
            table.add_row(
                muscle.title(),
                str(data["sets"]),
                f"[{status_color}]{data['status']}[/{status_color}]",
                data["last_trained"],
                f"{readiness['score']}/10"
            )

        self.console.print(table)

    def _display_exercise_tracking(self):
        """Display Tier 3 exercise-level tracking."""
        self.console.print("\n[bold]EXERCISE TRACKING[/bold]")

        table = Table(show_header=True, header_style="bold")
        table.add_column("Exercise")
        table.add_column("Peak e1RM", justify="right")
        table.add_column("Current e1RM", justify="right")
        table.add_column("Trend")
        table.add_column("Status")

        progression = self.get_progressive_overload_status()
        for exercise, data in progression.items():
            trend_icon = "▲" if data["trend_pct"] > 0 else "▼" if data["trend_pct"] < 0 else "→"
            trend_color = "green" if data["trend_pct"] > 0 else "red" if data["trend_pct"] < 0 else "yellow"

            status = data["status"]
            status_color = "green" if status == ProgressionStatus.ON_TRACK else "yellow" if status == ProgressionStatus.PLATEAU else "red"

            table.add_row(
                exercise,
                f"{data['peak_e1rm']} lbs",
                f"{data['current_e1rm']} lbs",
                f"[{trend_color}]{trend_icon} {data['trend_pct']:+.1f}%[/{trend_color}]",
                f"[{status_color}]{status.value}[/{status_color}]"
            )

        self.console.print(table)

    def _display_alerts(self):
        """Display alerts and recommendations."""
        alerts = self.generate_alerts()
        if not alerts:
            return

        self.console.print("\n[bold]ALERTS[/bold]")

        for alert in alerts:
            color = "red" if alert.priority == AlertPriority.HIGH else "yellow" if alert.priority == AlertPriority.MEDIUM else "green"
            self.console.print(f"[{color}][{alert.priority.value}][/{color}] {alert.alert_type}: {alert.message}")
            self.console.print(f"  → Action: {alert.action}")

    def _display_next_workout(self):
        """Display next workout recommendation."""
        workout = self.generate_next_workout()

        self.console.print("\n")
        self.console.print(Panel.fit(
            f"[bold]NEXT WORKOUT RECOMMENDATION[/bold]\n"
            f"Focus: {workout['focus']}\n"
            f"Duration: {workout['estimated_duration']}"
        ))

        table = Table(show_header=True, header_style="bold")
        table.add_column("#")
        table.add_column("Exercise")
        table.add_column("Target")
        table.add_column("Rest")
        table.add_column("Rationale")

        for i, ex in enumerate(workout["exercises"], 1):
            table.add_row(
                str(i),
                f"{ex['exercise']} ({ex['muscle'].title()})",
                f"{ex['target_weight']} lbs × {ex['target_reps']} reps × {ex['target_sets']} sets",
                f"{ex['rest_minutes']} min",
                ex["rationale"]
            )

        self.console.print(table)

        if workout["muscles_to_avoid"]:
            self.console.print("\n[bold red]MUSCLES TO AVOID[/bold red]")
            for m in workout["muscles_to_avoid"]:
                self.console.print(f"  • {m['muscle'].title()}: {m['reason']}")

    # ==================== HELPER METHODS ====================

    def _infer_session_type(self, df: pd.DataFrame) -> str:
        """Infer session type from exercises."""
        exercises = df['Exercise'].str.lower().tolist()
        upper = any(e for e in exercises if any(u in e for u in ['bench', 'press', 'row', 'pull', 'curl']))
        lower = any(e for e in exercises if any(l in e for l in ['squat', 'leg', 'deadlift', 'lunge']))

        if upper and lower:
            return "full_body"
        elif upper:
            return "upper"
        elif lower:
            return "lower"
        return "full_body"

    def _infer_muscle_group(self, exercise: str) -> str:
        """Infer muscle group from exercise name."""
        exercise = exercise.lower()
        mappings = {
            "chest": ["bench", "chest", "fly", "pec"],
            "back": ["row", "pulldown", "pull-up", "lat"],
            "shoulders": ["press", "shoulder", "delt", "lateral"],
            "quads": ["squat", "leg press", "extension", "lunge"],
            "hamstrings": ["deadlift", "curl", "hamstring"],
            "biceps": ["bicep", "curl"],
            "triceps": ["tricep", "pushdown", "dip"],
        }
        for muscle, keywords in mappings.items():
            if any(k in exercise for k in keywords):
                return muscle
        return "other"

    def _determine_focus(self, muscles: list[str]) -> str:
        """Determine workout focus from available muscles."""
        upper = {"chest", "back", "shoulders", "biceps", "triceps"}
        lower = {"quads", "hamstrings", "glutes", "calves"}

        has_upper = any(m in upper for m in muscles)
        has_lower = any(m in lower for m in muscles)

        if has_upper and has_lower:
            return "Full Body"
        elif has_upper:
            return "Upper Body"
        elif has_lower:
            return "Lower Body"
        return "General"


def create_sample_data() -> FitnessDashboard:
    """Create sample workout data for demonstration."""
    dashboard = FitnessDashboard()

    # Sample workouts over 4 weeks
    sample_workouts = [
        # Week 1
        WorkoutEntry(
            date="2026-01-06", session_type="upper", duration_minutes=55,
            exercises=[
                ExerciseData("Bench Press", "chest", [
                    SetData(1, 135, 10, 7), SetData(2, 145, 8, 8), SetData(3, 145, 7, 8.5)
                ]),
                ExerciseData("Lat Pulldown", "back", [
                    SetData(1, 120, 10, 7), SetData(2, 130, 8, 7.5), SetData(3, 130, 8, 8)
                ]),
                ExerciseData("Overhead Press", "shoulders", [
                    SetData(1, 75, 10, 7), SetData(2, 85, 8, 8), SetData(3, 85, 6, 9)
                ]),
            ]
        ),
        WorkoutEntry(
            date="2026-01-09", session_type="lower", duration_minutes=50,
            exercises=[
                ExerciseData("Leg Press", "quads", [
                    SetData(1, 270, 12, 7), SetData(2, 315, 10, 8), SetData(3, 315, 8, 8.5)
                ]),
                ExerciseData("Romanian Deadlift", "hamstrings", [
                    SetData(1, 135, 10, 7), SetData(2, 155, 8, 8), SetData(3, 155, 8, 8)
                ]),
            ]
        ),
        # Week 2
        WorkoutEntry(
            date="2026-01-13", session_type="upper", duration_minutes=60,
            exercises=[
                ExerciseData("Bench Press", "chest", [
                    SetData(1, 145, 10, 7), SetData(2, 155, 8, 8), SetData(3, 155, 6, 9)
                ]),
                ExerciseData("Lat Pulldown", "back", [
                    SetData(1, 130, 10, 7), SetData(2, 140, 8, 8), SetData(3, 140, 7, 8.5)
                ]),
                ExerciseData("Overhead Press", "shoulders", [
                    SetData(1, 85, 10, 7), SetData(2, 90, 8, 8), SetData(3, 90, 6, 9)
                ]),
            ]
        ),
        WorkoutEntry(
            date="2026-01-16", session_type="lower", duration_minutes=55,
            exercises=[
                ExerciseData("Leg Press", "quads", [
                    SetData(1, 315, 10, 7), SetData(2, 340, 8, 8), SetData(3, 340, 8, 8.5)
                ]),
                ExerciseData("Romanian Deadlift", "hamstrings", [
                    SetData(1, 155, 10, 7), SetData(2, 165, 8, 8), SetData(3, 165, 7, 8.5)
                ]),
            ]
        ),
        # Week 3
        WorkoutEntry(
            date="2026-01-20", session_type="upper", duration_minutes=55,
            exercises=[
                ExerciseData("Bench Press", "chest", [
                    SetData(1, 155, 8, 7.5), SetData(2, 160, 6, 8.5), SetData(3, 160, 5, 9)
                ]),
                ExerciseData("Lat Pulldown", "back", [
                    SetData(1, 140, 10, 7), SetData(2, 145, 8, 8), SetData(3, 145, 7, 8.5)
                ]),
                ExerciseData("Overhead Press", "shoulders", [
                    SetData(1, 90, 8, 7.5), SetData(2, 95, 6, 8.5), SetData(3, 95, 5, 9)
                ]),
            ]
        ),
        WorkoutEntry(
            date="2026-01-23", session_type="lower", duration_minutes=50,
            exercises=[
                ExerciseData("Leg Press", "quads", [
                    SetData(1, 340, 10, 7.5), SetData(2, 360, 8, 8.5), SetData(3, 360, 6, 9)
                ]),
                ExerciseData("Romanian Deadlift", "hamstrings", [
                    SetData(1, 165, 10, 7), SetData(2, 175, 8, 8), SetData(3, 175, 7, 8.5)
                ]),
            ]
        ),
    ]

    for workout in sample_workouts:
        dashboard.add_workout(workout)

    # Add soreness data
    soreness_entries = [
        SorenessEntry("2026-01-24", "chest", 4, "Mild DOMS"),
        SorenessEntry("2026-01-24", "back", 3, "Light soreness"),
        SorenessEntry("2026-01-24", "quads", 6, "Moderate DOMS from leg day"),
        SorenessEntry("2026-01-24", "hamstrings", 5, "Moderate soreness"),
        SorenessEntry("2026-01-24", "shoulders", 2, "Minimal"),
    ]

    for soreness in soreness_entries:
        dashboard.add_soreness(soreness)

    return dashboard


def main():
    """Main entry point."""
    console = Console()

    console.print("[bold blue]Fitness Intelligence System[/bold blue]")
    console.print("Loading sample data for demonstration...\n")

    dashboard = create_sample_data()
    dashboard.display_dashboard()


if __name__ == "__main__":
    main()
