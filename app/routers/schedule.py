from fastapi import APIRouter, Query
from ..core.db import get_conn
from ..core.utils import week_range

router = APIRouter(prefix="/schedule", tags=["schedule"])

@router.get("/week")
def schedule_week(week_monday: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """室長用：週次（全体）"""
    d0, d1 = week_range(week_monday)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
              l.lesson_id, l.lesson_date, l.slot_no,
              l.student_code,
              (s.last_name || s.first_name) AS student_name,
              l.teacher_code,
              (t.last_name || t.first_name) AS teacher_name,
              COALESCE(sub.display_name, l.subject_text, '') AS subject_name,
              l.status
            FROM lessons l
            LEFT JOIN students s ON s.student_code = l.student_code
            LEFT JOIN teachers t ON t.teacher_code = l.teacher_code
            LEFT JOIN subjects sub ON sub.subject_id = l.subject_id
            WHERE l.lesson_date >= ? AND l.lesson_date < ?
            ORDER BY l.lesson_date, l.slot_no, l.teacher_code, l.student_code, l.lesson_id
            """,
            (d0, d1),
        ).fetchall()

    return {"week_monday": d0, "week_end": d1, "count": len(rows), "items": [dict(r) for r in rows]}

@router.get("/week/teacher")
def schedule_week_teacher(
    teacher_code: str = Query(..., min_length=1),
    week_monday: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """講師用：週次（自分のみ）"""
    d0, d1 = week_range(week_monday)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
              l.lesson_id, l.lesson_date, l.slot_no,
              l.student_code,
              (s.last_name || s.first_name) AS student_name,
              l.teacher_code,
              (t.last_name || t.first_name) AS teacher_name,
              COALESCE(sub.display_name, l.subject_text, '') AS subject_name,
              l.status
            FROM lessons l
            LEFT JOIN students s ON s.student_code = l.student_code
            LEFT JOIN teachers t ON t.teacher_code = l.teacher_code
            LEFT JOIN subjects sub ON sub.subject_id = l.subject_id
            WHERE l.teacher_code = ?
              AND l.lesson_date >= ? AND l.lesson_date < ?
            ORDER BY l.lesson_date, l.slot_no, l.student_code, l.lesson_id
            """,
            (teacher_code, d0, d1),
        ).fetchall()

    return {"teacher_code": teacher_code, "week_monday": d0, "week_end": d1, "count": len(rows), "items": [dict(r) for r in rows]}
