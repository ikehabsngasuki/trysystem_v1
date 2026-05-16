#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import sqlite3
from datetime import datetime, date, timedelta
from typing import Optional

DB_PATH = "school_v1.db"

app = FastAPI(title="塾 基幹システム MVP", version="0.1")


def parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def week_range(week_monday: str) -> tuple[str, str]:
    d0 = parse_ymd(week_monday)
    d1 = d0 + timedelta(days=7)
    return d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d")


def extract_from_lesson_id_from_after_json(after_json: str) -> Optional[int]:
    """
    change_log.after_json は str(dict) で保存されている前提。
    例: "{'from_after': {...}, 'to_after': {...}, 'from_lesson_id': 197, 'to_lesson_id': 555}"
    ここから from_lesson_id の数値だけ抜く。
    """
    if not after_json:
        return None
    marker = "'from_lesson_id':"
    if marker not in after_json:
        return None
    try:
        tail = after_json.split(marker, 1)[1]
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        return int(digits) if digits else None
    except Exception:
        return None


class LessonCreate(BaseModel):
    lesson_date: str  # YYYY-MM-DD
    slot_no: int
    student_code: str
    teacher_code: Optional[str] = None
    subject_id: Optional[int] = None
    subject_text: Optional[str] = None
    status: str = "scheduled"
    source_enrollment_slot_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    note: Optional[str] = None


class RescheduleRequest(BaseModel):
    from_lesson_id: int

    to_lesson_date: str  # YYYY-MM-DD
    to_slot_no: int
    to_teacher_code: Optional[str] = None  # 未定ならNoneでも可

    # 指定があれば優先、なければ元を引き継ぐ（subject_id優先）
    to_subject_id: Optional[int] = None
    to_subject_text: Optional[int | str] = None  # 文字も来うるので広めに

    updated_by_user_id: Optional[int] = None
    note: Optional[str] = None


class RescheduleCancelRequest(BaseModel):
    # 振替先（新しく作られた方）の lesson_id を渡す
    to_lesson_id: int
    updated_by_user_id: Optional[int] = None
    note: Optional[str] = None


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/schedule/week")
def schedule_week(
    week_monday: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
):
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

    return {
        "week_monday": d0,
        "week_end": d1,
        "count": len(rows),
        "items": [dict(r) for r in rows],
    }


@app.get("/schedule/week/teacher")
def schedule_week_teacher(
    teacher_code: str = Query(..., min_length=1),
    week_monday: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
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

    return {
        "teacher_code": teacher_code,
        "week_monday": d0,
        "week_end": d1,
        "count": len(rows),
        "items": [dict(r) for r in rows],
    }


@app.post("/lessons", status_code=201)
def create_lesson(payload: LessonCreate):
    """室長用：lesson追加（振替先の追加・期間追加コマの追加に使う）"""
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO lessons(
                  lesson_date, slot_no, student_code, teacher_code,
                  subject_id, subject_text, status, source_enrollment_slot_id,
                  updated_by_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.lesson_date,
                    payload.slot_no,
                    payload.student_code,
                    payload.teacher_code,
                    payload.subject_id,
                    payload.subject_text,
                    payload.status,
                    payload.source_enrollment_slot_id,
                    payload.updated_by_user_id,
                ),
            )
            lesson_id = cur.lastrowid

            conn.execute(
                """
                INSERT INTO change_log(user_id, action, lesson_id, note, after_json)
                VALUES (?, 'lesson_insert', ?, ?, ?)
                """,
                (payload.updated_by_user_id, lesson_id, payload.note, str(payload.model_dump())),
            )

            conn.commit()
            return {"lesson_id": lesson_id}
        except sqlite3.IntegrityError as e:
            raise HTTPException(status_code=409, detail=str(e))


@app.post("/lessons/{lesson_id}/cancel")
def cancel_lesson(
    lesson_id: int, updated_by_user_id: Optional[int] = None, note: Optional[str] = None
):
    """室長用：lessonキャンセル（振替元のキャンセルに使う）"""
    with get_conn() as conn:
        before = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
        if not before:
            raise HTTPException(status_code=404, detail="lesson not found")

        if before["status"] == "cancelled":
            return {"lesson_id": lesson_id, "status": "cancelled"}

        try:
            conn.execute(
                "UPDATE lessons SET status='cancelled', updated_at=datetime('now'), updated_by_user_id=? WHERE lesson_id=?",
                (updated_by_user_id, lesson_id),
            )
            after = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()

            conn.execute(
                """
                INSERT INTO change_log(user_id, action, lesson_id, note, before_json, after_json)
                VALUES (?, 'lesson_cancel', ?, ?, ?, ?)
                """,
                (updated_by_user_id, lesson_id, note, str(dict(before)), str(dict(after))),
            )

            conn.commit()
            return {"lesson_id": lesson_id, "status": "cancelled"}
        except sqlite3.IntegrityError as e:
            raise HTTPException(status_code=409, detail=str(e))


@app.post("/lessons/{lesson_id}/restore")
def restore_lesson(
    lesson_id: int, updated_by_user_id: Optional[int] = None, note: Optional[str] = None
):
    """室長用：lesson復活（cancelled → scheduled）"""
    with get_conn() as conn:
        before = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
        if not before:
            raise HTTPException(status_code=404, detail="lesson not found")

        if before["status"] == "scheduled":
            return {"lesson_id": lesson_id, "status": "scheduled"}

        try:
            conn.execute(
                "UPDATE lessons SET status='scheduled', updated_at=datetime('now'), updated_by_user_id=? WHERE lesson_id=?",
                (updated_by_user_id, lesson_id),
            )
            after = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()

            conn.execute(
                """
                INSERT INTO change_log(user_id, action, lesson_id, note, before_json, after_json)
                VALUES (?, 'lesson_restore', ?, ?, ?, ?)
                """,
                (updated_by_user_id, lesson_id, note, str(dict(before)), str(dict(after))),
            )

            conn.commit()
            return {"lesson_id": lesson_id, "status": "scheduled"}
        except sqlite3.IntegrityError as e:
            raise HTTPException(status_code=409, detail=str(e))


@app.post("/lessons/reschedule", status_code=201)
def reschedule_lesson(payload: RescheduleRequest):
    """
    室長用：振替（1トランザクション）
    - 先に振替先を作成 → 成功したら振替元をcancel
    - 途中で失敗したらロールバック（元は残る）
    """
    try:
        _ = parse_ymd(payload.to_lesson_date)
    except Exception:
        raise HTTPException(status_code=422, detail="to_lesson_date must be YYYY-MM-DD")

    with get_conn() as conn:
        try:
            conn.execute("BEGIN;")

            before = conn.execute(
                """
                SELECT
                  l.*,
                  COALESCE(sub.display_name, l.subject_text, '') AS subject_name
                FROM lessons l
                LEFT JOIN subjects sub ON sub.subject_id = l.subject_id
                WHERE l.lesson_id=?
                """,
                (payload.from_lesson_id,),
            ).fetchone()

            if not before:
                raise HTTPException(status_code=404, detail="from lesson not found")

            if before["status"] != "scheduled":
                raise HTTPException(status_code=409, detail="from lesson is not scheduled")

            # 科目決定（指定があれば優先、なければ元を引き継ぐ）
            to_subject_id = payload.to_subject_id
            to_subject_text = payload.to_subject_text
            if to_subject_id is None and to_subject_text is None:
                to_subject_id = before["subject_id"]
                to_subject_text = before["subject_text"] or before["subject_name"] or None

            cur = conn.execute(
                """
                INSERT INTO lessons(
                  lesson_date, slot_no, student_code, teacher_code,
                  subject_id, subject_text, status, source_enrollment_slot_id,
                  updated_by_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, 'scheduled', NULL, ?)
                """,
                (
                    payload.to_lesson_date,
                    payload.to_slot_no,
                    before["student_code"],
                    payload.to_teacher_code,
                    to_subject_id,
                    str(to_subject_text) if to_subject_text is not None else None,
                    payload.updated_by_user_id,
                ),
            )
            new_lesson_id = cur.lastrowid

            conn.execute(
                """
                UPDATE lessons
                SET status='cancelled', updated_at=datetime('now'), updated_by_user_id=?
                WHERE lesson_id=?
                """,
                (payload.updated_by_user_id, payload.from_lesson_id),
            )

            after_new = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (new_lesson_id,)).fetchone()
            after_old = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (payload.from_lesson_id,)).fetchone()

            conn.execute(
                """
                INSERT INTO change_log(user_id, action, lesson_id, note, before_json, after_json)
                VALUES (?, 'lesson_reschedule', ?, ?, ?, ?)
                """,
                (
                    payload.updated_by_user_id,
                    new_lesson_id,
                    payload.note,
                    str(dict(before)),
                    str(
                        {
                            "from_after": dict(after_old),
                            "to_after": dict(after_new),
                            "from_lesson_id": payload.from_lesson_id,
                            "to_lesson_id": new_lesson_id,
                        }
                    ),
                ),
            )

            conn.execute("COMMIT;")
            return {"from_lesson_id": payload.from_lesson_id, "to_lesson_id": new_lesson_id, "status": "ok"}

        except HTTPException:
            conn.execute("ROLLBACK;")
            raise
        except sqlite3.IntegrityError as e:
            conn.execute("ROLLBACK;")
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            conn.execute("ROLLBACK;")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/lessons/reschedule/cancel")
def cancel_reschedule(payload: RescheduleCancelRequest):
    """
    室長用：振替取消（1トランザクション）
    入力は to_lesson_id（振替先）だけでOK。
    change_log(action='lesson_reschedule', lesson_id=to_lesson_id) から from_lesson_id を逆引きする。
    """
    with get_conn() as conn:
        try:
            conn.execute("BEGIN;")

            # to_lesson_id の状態
            to_row = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (payload.to_lesson_id,)).fetchone()
            if not to_row:
                raise HTTPException(status_code=404, detail="to lesson not found")

            # 逆引きログ
            log = conn.execute(
                """
                SELECT change_id, before_json, after_json
                FROM change_log
                WHERE action='lesson_reschedule' AND lesson_id=?
                ORDER BY change_id DESC
                LIMIT 1
                """,
                (payload.to_lesson_id,),
            ).fetchone()
            if not log:
                raise HTTPException(status_code=404, detail="reschedule log not found for to_lesson_id")

            from_lesson_id = extract_from_lesson_id_from_after_json(log["after_json"] or "")
            if from_lesson_id is None:
                raise HTTPException(status_code=409, detail="cannot parse from_lesson_id from change_log.after_json")

            from_row = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (from_lesson_id,)).fetchone()
            if not from_row:
                raise HTTPException(status_code=404, detail="from lesson not found")

            # to をキャンセル（scheduled/cancelledどちらでもOK扱い）
            conn.execute(
                "UPDATE lessons SET status='cancelled', updated_at=datetime('now'), updated_by_user_id=? WHERE lesson_id=?",
                (payload.updated_by_user_id, payload.to_lesson_id),
            )

            # from を復活（ここで衝突するなら409で戻す）
            conn.execute(
                "UPDATE lessons SET status='scheduled', updated_at=datetime('now'), updated_by_user_id=? WHERE lesson_id=?",
                (payload.updated_by_user_id, from_lesson_id),
            )

            after_to = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (payload.to_lesson_id,)).fetchone()
            after_from = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (from_lesson_id,)).fetchone()

            conn.execute(
                """
                INSERT INTO change_log(user_id, action, lesson_id, note, before_json, after_json)
                VALUES (?, 'lesson_reschedule_cancel', ?, ?, ?, ?)
                """,
                (
                    payload.updated_by_user_id,
                    payload.to_lesson_id,
                    payload.note,
                    str({"from_before": dict(from_row), "to_before": dict(to_row)}),
                    str({"from_after": dict(after_from), "to_after": dict(after_to), "from_lesson_id": from_lesson_id}),
                ),
            )

            conn.execute("COMMIT;")
            return {"status": "ok", "from_lesson_id": from_lesson_id, "to_lesson_id": payload.to_lesson_id}

        except HTTPException:
            conn.execute("ROLLBACK;")
            raise
        except sqlite3.IntegrityError as e:
            conn.execute("ROLLBACK;")
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            conn.execute("ROLLBACK;")
            raise HTTPException(status_code=500, detail=str(e))
