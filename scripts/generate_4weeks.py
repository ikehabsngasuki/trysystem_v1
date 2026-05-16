#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sqlite3
from datetime import date, datetime, timedelta

def parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def db_day_of_week(d: date) -> int:
    # DB: 1..7 (1=Mon .. 7=Sun) を想定
    return d.weekday() + 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--week_monday", required=True)  # YYYY-MM-DD
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    week_monday = parse_ymd(args.week_monday)
    end_date = week_monday + timedelta(days=28)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    slots = conn.execute(
        """
        SELECT enrollment_slot_id, student_code, day_of_week, slot_no,
               teacher_code, subject_id, subject
        FROM enrollment_slots
        WHERE student_code IS NOT NULL
        """
    ).fetchall()

    to_insert = []
    for r in slots:
        d = week_monday
        while d < end_date:
            if db_day_of_week(d) == int(r["day_of_week"]):
                lesson_date = d.strftime("%Y-%m-%d")

                # その生徒のその日そのコマが既にあるならスキップ（例外がある可能性）
                exists = conn.execute(
                    """
                    SELECT 1 FROM lessons
                    WHERE lesson_date=? AND slot_no=? AND student_code=?
                    LIMIT 1
                    """,
                    (lesson_date, int(r["slot_no"]), r["student_code"]),
                ).fetchone()

                if not exists:
                    to_insert.append((
                        lesson_date,
                        int(r["slot_no"]),
                        r["student_code"],
                        r["teacher_code"],
                        r["subject_id"],
                        r["subject"],
                        "scheduled",
                        int(r["enrollment_slot_id"]),
                    ))
            d += timedelta(days=1)

    print("week_monday:", week_monday)
    print("range:", week_monday, "->", end_date, "(4 weeks)")
    print("to_insert:", len(to_insert))

    if not args.write:
        print("dry-run only. use --write to apply.")
        return

    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO lessons(
                  lesson_date, slot_no, student_code, teacher_code,
                  subject_id, subject_text, status, source_enrollment_slot_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                to_insert,
            )
        print("inserted:", len(to_insert))
    except sqlite3.IntegrityError as e:
        print("[ERROR] integrity error:", e)
        print("Hint: duplicate booking detected by UNIQUE constraints.")
        raise

if __name__ == "__main__":
    main()
