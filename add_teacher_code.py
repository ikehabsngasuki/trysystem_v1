#!/usr/bin/env python3
import argparse
import sqlite3


def ensure_columns(cur: sqlite3.Cursor):
    cur.execute("PRAGMA table_info(teachers);")
    cols = {row[1] for row in cur.fetchall()}
    missing = [c for c in ("teacher_id", "teacher_code") if c not in cols]
    if missing:
        raise SystemExit(f"[ERROR] teachers table missing columns: {missing}")


def main():
    ap = argparse.ArgumentParser(description="Fill teachers.teacher_code from teacher_id (e.g., 14 -> T0014).")
    ap.add_argument("--db", required=True, help="SQLite DB path (e.g. school_v1.db)")
    ap.add_argument("--width", type=int, default=4, help="Zero-pad width (default: 4 -> T0001)")
    ap.add_argument("--prefix", default="T", help="Code prefix (default: T)")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change, but do not write")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON;")

    ensure_columns(cur)

    # 対象: teacher_code が NULL or 空文字の行だけ
    rows = cur.execute(
        """
        SELECT teacher_id, teacher_code
        FROM teachers
        WHERE teacher_code IS NULL OR TRIM(teacher_code) = ''
        ORDER BY teacher_id
        """
    ).fetchall()

    if not rows:
        print("No rows to update (teacher_code already filled).")
        conn.close()
        return

    # 変更内容を表示
    print(f"Target rows: {len(rows)}")
    preview_n = min(20, len(rows))
    print(f"Preview first {preview_n}:")
    for r in rows[:preview_n]:
        tid = int(r["teacher_id"])
        new_code = f"{args.prefix}{tid:0{args.width}d}"
        print(f"  teacher_id={tid} -> teacher_code={new_code}")

    if args.dry_run:
        print("(dry-run: no changes written)")
        conn.close()
        return

    # 一括更新（SQLiteでゼロ埋め）
    cur.execute(
        """
        UPDATE teachers
        SET teacher_code = ? || printf('%0*d', ?, teacher_id),
            updated_at = datetime('now')
        WHERE teacher_code IS NULL OR TRIM(teacher_code) = ''
        """,
        (args.prefix, args.width),
    )

    conn.commit()
    updated = cur.rowcount
    conn.close()

    print("=== fill result ===")
    print(f"updated: {updated}")


if __name__ == "__main__":
    main()
