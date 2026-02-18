#!/usr/bin/env python3
import argparse
import csv
import sqlite3

VALID_STATUS = {"active", "retired"}

def to_int_or_none(s: str):
    if s is None:
        return None
    s = s.strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None

def norm_str(s: str):
    if s is None:
        return None
    s = s.strip()
    return s if s != "" else None

def norm_status(s: str):
    s = (s or "").strip()
    if s == "":
        return None  # DB default 'active' を使う
    if s in VALID_STATUS:
        return s
    return "active"

def make_student_code(student_id: int, width: int = 4) -> str:
    return f"S{student_id:0{width}d}"

def ensure_student_code_column(cur: sqlite3.Cursor):
    """
    student_code 列が存在し、NOT NULL であることをチェック。
    """
    cur.execute("PRAGMA table_info(students);")
    rows = cur.fetchall()
    colmap = {r[1]: r for r in rows}  # name -> row
    if "student_code" not in colmap:
        raise SystemExit(
            "[ERROR] students.student_code column not found.\n"
            "Run migration to add NOT NULL student_code."
        )
    # row: (cid, name, type, notnull, dflt_value, pk)
    notnull = int(colmap["student_code"][3])
    if notnull != 1:
        raise SystemExit(
            "[ERROR] students.student_code is not NOT NULL.\n"
            "Run migration to enforce NOT NULL."
        )

def next_student_id(cur: sqlite3.Cursor) -> int:
    """
    student_id が無い行を挿入するために、次に使う student_id を確保する。
    AUTOINCREMENT なら sqlite_sequence を優先、無ければ MAX+1。
    ※単一プロセスでのインポート前提（並列実行しない）。
    """
    try:
        cur.execute("SELECT seq FROM sqlite_sequence WHERE name='students';")
        row = cur.fetchone()
        if row and row[0] is not None:
            return int(row[0]) + 1
    except sqlite3.Error:
        pass

    cur.execute("SELECT IFNULL(MAX(student_id), 0) + 1 FROM students;")
    return int(cur.fetchone()[0])

def build_insert_sql(include_student_id: bool):
    cols = []
    params = []
    if include_student_id:
        cols.append("student_id")
        params.append(":student_id")

    # created_at / updated_at はDBデフォルトを使うので入れない
    cols += [
        "student_code",
        "first_name",
        "first_name_yomi",
        "last_name",
        "last_name_yomi",
        "school_name",
        "school_year",
        "status",
    ]
    params += [
        ":student_code",
        ":first_name",
        ":first_name_yomi",
        ":last_name",
        ":last_name_yomi",
        ":school_name",
        ":school_year",
        ":status",
    ]

    return f"""
    INSERT INTO students ({", ".join(cols)})
    VALUES ({", ".join(params)})
    """

def build_upsert_sql():
    # student_id がある行だけ upsert
    return """
    INSERT INTO students (
      student_id,
      student_code,
      first_name, first_name_yomi,
      last_name, last_name_yomi,
      school_name, school_year,
      status
    )
    VALUES (
      :student_id,
      :student_code,
      :first_name, :first_name_yomi,
      :last_name, :last_name_yomi,
      :school_name, :school_year,
      :status
    )
    ON CONFLICT(student_id) DO UPDATE SET
      student_code     = excluded.student_code,
      first_name       = excluded.first_name,
      first_name_yomi  = excluded.first_name_yomi,
      last_name        = excluded.last_name,
      last_name_yomi   = excluded.last_name_yomi,
      school_name      = excluded.school_name,
      school_year      = excluded.school_year,
      status           = excluded.status,
      updated_at       = datetime('now')
    """

def main():
    ap = argparse.ArgumentParser(description="Import students CSV into SQLite students table")
    ap.add_argument("--db", required=True, help="SQLite DB path (e.g. school_v1.db)")
    ap.add_argument("--csv", required=True, help="CSV path (UTF-8/UTF-8-SIG recommended)")
    ap.add_argument("--mode", choices=["insert", "upsert"], default="upsert",
                    help="insert: always insert / upsert: use student_id to upsert when present (default)")
    ap.add_argument("--dry-run", action="store_true", help="parse only, do not write to DB")
    ap.add_argument("--code-width", type=int, default=4, help="S code zero-pad width (default 4 => S0001)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA foreign_keys=ON;")

    ensure_student_code_column(cur)

    inserted = 0
    updated = 0
    skipped = 0
    warned = 0

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        expected = ["student_id","first_name","first_name_yomi","last_name","last_name_yomi","school_name","school_year","status"]
        missing = [c for c in expected if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"CSV header missing columns: {missing}\nFound: {reader.fieldnames}")

        # SQL準備
        sql_upsert = build_upsert_sql()
        sql_insert_withid = build_insert_sql(include_student_id=True)
        sql_insert_noid = build_insert_sql(include_student_id=False)

        for line_no, row in enumerate(reader, start=2):
            student_id = to_int_or_none(row.get("student_id"))
            first_name = norm_str(row.get("first_name"))
            last_name  = norm_str(row.get("last_name"))

            if not first_name or not last_name:
                skipped += 1
                warned += 1
                print(f"[WARN] line {line_no}: first_name/last_name is required -> skipped")
                continue

            first_name_yomi = norm_str(row.get("first_name_yomi"))
            last_name_yomi  = norm_str(row.get("last_name_yomi"))
            school_name     = norm_str(row.get("school_name"))
            school_year_raw = row.get("school_year")
            school_year     = to_int_or_none(school_year_raw)
            if school_year is not None and not (1 <= school_year <= 12):
                warned += 1
                print(f"[WARN] line {line_no}: school_year out of range (1-12): {school_year_raw!r} -> set NULL")
                school_year = None

            status = norm_status(row.get("status"))

            # ★NOT NULL対応：必ず student_code を用意する
            if student_id is None:
                # 次のIDを確保して code を作る（INSERT時点で入れる）
                student_id = next_student_id(cur)

            student_code = make_student_code(student_id, width=args.code_width)

            params = {
                "student_id": student_id,
                "student_code": student_code,
                "first_name": first_name,
                "first_name_yomi": first_name_yomi,
                "last_name": last_name,
                "last_name_yomi": last_name_yomi,
                "school_name": school_name,
                "school_year": school_year,
                "status": status,
            }

            if args.dry_run:
                continue

            try:
                if args.mode == "upsert":
                    # student_id 基準で upsert（既存なら更新、無ければ挿入）
                    cur.execute(sql_upsert, params)
                    updated += 1
                else:
                    # insertモード：student_idがCSVに無い場合はDB採番に任せる選択もあるが、
                    # NOT NULL student_code のため、ここでは明示IDで入れる運用に統一
                    cur.execute(sql_insert_withid, params)
                    inserted += 1

            except sqlite3.IntegrityError as e:
                skipped += 1
                warned += 1
                print(f"[WARN] line {line_no}: IntegrityError: {e} -> skipped")
            except sqlite3.Error as e:
                raise SystemExit(f"[ERROR] line {line_no}: sqlite error: {e}")

    if not args.dry_run:
        conn.commit()
    conn.close()

    print("=== import result ===")
    print(f"inserted: {inserted}")
    print(f"updated : {updated} (upsert attempts; may include inserts)")
    print(f"skipped : {skipped}")
    print(f"warned  : {warned}")
    if args.dry_run:
        print("(dry-run: no changes written)")

if __name__ == "__main__":
    main()
