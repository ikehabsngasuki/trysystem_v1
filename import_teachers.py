#!/usr/bin/env python3
import argparse
import csv
import sqlite3
from typing import Optional, Dict, Any, List


def norm(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = s.strip()
    return s if s != "" else None


def sniff_dialect(path: str, encoding: str) -> csv.Dialect:
    # 先頭を読んで区切りを推測（タブ/カンマどちらでも）
    with open(path, "r", encoding=encoding, newline="") as f:
        sample = f.read(8192)
    sniffer = csv.Sniffer()
    try:
        return sniffer.sniff(sample, delimiters=",\t")
    except csv.Error:
        # 推測失敗時はタブ優先（ユーザーの例はタブっぽい）
        class Tsv(csv.Dialect):
            delimiter = "\t"
            quotechar = '"'
            doublequote = True
            skipinitialspace = False
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL
        return Tsv()


def ensure_schema(conn: sqlite3.Connection):
    # 念のため teachers テーブルがあるか確認
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='teachers';")
    if cur.fetchone() is None:
        raise RuntimeError("teachers テーブルが見つかりません。DBパスが正しいか確認してください。")


def insert_rows(conn: sqlite3.Connection, rows: List[Dict[str, Any]], upsert: bool):
    """
    teacher_id がある行：teacher_id を指定してINSERT
    teacher_id がない行：teacher_id を省略してINSERT（AUTOINCREMENT）
    upsert=True の場合：teacher_id が既にあれば UPDATE に回す（teacher_id必須）
    """
    inserted = 0
    updated = 0

    for r in rows:
        teacher_id = r.get("teacher_id")
        first_name = r["first_name"]
        last_name = r["last_name"]
        status = r.get("status") or "active"

        if upsert:
            if teacher_id is None:
                raise ValueError("upsert を使う場合、CSVの teacher_id は必須です。")
            # まずUPDATE
            cur = conn.execute(
                """
                UPDATE teachers
                   SET first_name=?,
                       first_name_yomi=?,
                       last_name=?,
                       last_name_yomi=?,
                       status=?,
                       updated_at=datetime('now')
                 WHERE teacher_id=?
                """,
                (
                    first_name,
                    r.get("first_name_yomi"),
                    last_name,
                    r.get("last_name_yomi"),
                    status,
                    teacher_id,
                ),
            )
            if cur.rowcount == 1:
                updated += 1
                continue
            # なければINSERT
            conn.execute(
                """
                INSERT INTO teachers (
                  teacher_id,
                  first_name, first_name_yomi,
                  last_name,  last_name_yomi,
                  status
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    teacher_id,
                    first_name,
                    r.get("first_name_yomi"),
                    last_name,
                    r.get("last_name_yomi"),
                    status,
                ),
            )
            inserted += 1
            continue

        # upsert しない通常モード
        if teacher_id is not None:
            conn.execute(
                """
                INSERT INTO teachers (
                  teacher_id,
                  first_name, first_name_yomi,
                  last_name,  last_name_yomi,
                  status
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    teacher_id,
                    first_name,
                    r.get("first_name_yomi"),
                    last_name,
                    r.get("last_name_yomi"),
                    status,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO teachers (
                  first_name, first_name_yomi,
                  last_name,  last_name_yomi,
                  status
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    first_name,
                    r.get("first_name_yomi"),
                    last_name,
                    r.get("last_name_yomi"),
                    status,
                ),
            )
        inserted += 1

    return inserted, updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="SQLite DB path (例: school.db)")
    ap.add_argument("csvfile", help="CSV/TSV path (例: watanbe_teacher.csv)")
    ap.add_argument("--encoding", default="utf-8-sig", help="default: utf-8-sig")
    ap.add_argument("--upsert", action="store_true", help="teacher_idが既にあれば更新、なければ追加")
    args = ap.parse_args()

    dialect = sniff_dialect(args.csvfile, args.encoding)

    with open(args.csvfile, "r", encoding=args.encoding, newline="") as f:
        reader = csv.DictReader(f, dialect=dialect)
        required = {"teacher_id", "first_name", "first_name_yomi", "last_name", "last_name_yomi", "status"}
        if not reader.fieldnames:
            raise RuntimeError("ヘッダー行が読めませんでした。")
        missing = required - set(reader.fieldnames)
        if missing:
            raise RuntimeError(f"CSVヘッダーに不足があります: {sorted(missing)}")

        rows: List[Dict[str, Any]] = []
        for i, row in enumerate(reader, start=2):
            teacher_id_raw = norm(row.get("teacher_id"))
            teacher_id = int(teacher_id_raw) if teacher_id_raw is not None else None

            first_name = norm(row.get("first_name"))
            last_name = norm(row.get("last_name"))
            if not first_name or not last_name:
                raise ValueError(f"{i}行目: first_name/last_name は必須です。")

            rows.append(
                {
                    "teacher_id": teacher_id,
                    "first_name": first_name,
                    "first_name_yomi": norm(row.get("first_name_yomi")),
                    "last_name": last_name,
                    "last_name_yomi": norm(row.get("last_name_yomi")),
                    "status": norm(row.get("status")) or "active",
                }
            )

    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA foreign_keys=ON;")
        ensure_schema(conn)

        conn.execute("BEGIN;")
        inserted, updated = insert_rows(conn, rows, upsert=args.upsert)
        conn.commit()

        print(f"OK: inserted={inserted}, updated={updated}, total={len(rows)}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
