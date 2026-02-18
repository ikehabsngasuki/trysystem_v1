#!/usr/bin/env python3
import argparse
import csv
import re
import sqlite3
from typing import Optional, Tuple

WS_SPLIT = re.compile(r"[ \t\u3000]+")  # 半角/タブ/全角スペース

def norm(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = s.strip()
    return s if s else None

def split_name(full: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    "苗字<空白類>名前" を (last_name, first_name) に分割。
    空白類: 半角スペース / タブ / 全角スペース
    空白が無い場合は (full, None)。
    """
    full = norm(full)
    if not full:
        return None, None
    parts = [p for p in WS_SPLIT.split(full) if p]
    if len(parts) >= 2:
        return parts[0], "".join(parts[1:])
    return full, None

def ensure_students_columns(cur: sqlite3.Cursor):
    cur.execute("PRAGMA table_info(students);")
    cols = {row[1] for row in cur.fetchall()}
    required = {"last_name", "first_name", "last_name_yomi", "first_name_yomi"}
    missing = sorted(required - cols)
    if missing:
        raise SystemExit(f"[ERROR] students table missing columns: {missing}")

INSERT_SQL = """
INSERT INTO students (
  last_name, first_name,
  last_name_yomi, first_name_yomi
)
VALUES (
  :last_name, :first_name,
  :last_name_yomi, :first_name_yomi
)
"""

def open_dict_reader(path: str, encoding: str):
    """
    CSV/TSVを自動判定してDictReaderを返す
    """
    f = open(path, newline="", encoding=encoding)
    sample = f.read(4096)
    f.seek(0)

    # Snifferで区切りを推定（失敗したらカンマ）
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t"])
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ","

    reader = csv.DictReader(f, dialect=dialect)
    return f, reader, dialect.delimiter

def main():
    ap = argparse.ArgumentParser(description="Insert student names (kanji/kana) into students table from CSV/TSV.")
    ap.add_argument("--db", required=True, help="SQLite DB path (e.g. school_v1.db)")
    ap.add_argument("--csv", required=True, help="CSV/TSV path (e.g. 202602_student_list.csv)")
    ap.add_argument("--encoding", default="utf-8-sig", help="default: utf-8-sig (try cp932 if Windows Excel)")
    ap.add_argument("--dry-run", action="store_true", help="parse only, do not write to DB")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON;")

    ensure_students_columns(cur)

    inserted = 0
    skipped = 0
    warned = 0

    f, reader, delim = open_dict_reader(args.csv, args.encoding)

    try:
        # 必須列名
        required_cols = ["生徒氏名", "生徒氏名（かな）"]
        missing = [c for c in required_cols if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(
                f"[ERROR] header missing columns: {missing}\n"
                f"Detected delimiter={delim!r}\n"
                f"Found headers: {reader.fieldnames}"
            )

        for line_no, row in enumerate(reader, start=2):
            last_name, first_name = split_name(row.get("生徒氏名"))
            last_yomi, first_yomi = split_name(row.get("生徒氏名（かな）"))

            if not last_name or not first_name:
                warned += 1
                print(f"[WARN] line {line_no}: 生徒氏名 split failed or first_name missing: {row.get('生徒氏名')!r}")
            if row.get("生徒氏名（かな）") and (not last_yomi or not first_yomi):
                warned += 1
                print(f"[WARN] line {line_no}: 生徒氏名（かな） split failed: {row.get('生徒氏名（かな）')!r}")

            # 苗字が無い行はスキップ（運用で変更可）
            if not last_name:
                skipped += 1
                warned += 1
                print(f"[WARN] line {line_no}: last_name empty -> skipped")
                continue

            params = {
                "last_name": last_name,
                "first_name": first_name,
                "last_name_yomi": last_yomi,
                "first_name_yomi": first_yomi,
            }

            if args.dry_run:
                continue

            cur.execute(INSERT_SQL, params)
            inserted += 1

        if not args.dry_run:
            conn.commit()

    finally:
        f.close()
        conn.close()

    print("=== import result ===")
    print(f"inserted: {inserted}")
    print(f"skipped : {skipped}")
    print(f"warned  : {warned}")
    if args.dry_run:
        print("(dry-run: no changes written)")

if __name__ == "__main__":
    main()
