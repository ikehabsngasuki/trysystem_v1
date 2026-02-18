#!/usr/bin/env python3
import argparse
import csv
import sqlite3

VALID_STATUS = {"active", "retired"}

def norm_str(s):
    if s is None:
        return None
    s = s.strip()
    return s if s != "" else None

def norm_status(s):
    s = (s or "").strip()
    if s == "":
        return None  # DB default
    return s if s in VALID_STATUS else "active"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--width", type=int, default=4)  # T0001の桁
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON;")

    # code列がまだ無い/作ってない場合に備える（すでに作ってるならそのまま通る）
    # ※ 既に列があるとALTERは失敗するので、ここではDB側で作ってある前提が安全
    # 必要なら `.schema teachers` で確認してね

    sql_insert = """
    INSERT INTO teachers(
      first_name, first_name_yomi,
      last_name,  last_name_yomi,
      status
    )
    VALUES(
      :first_name, :first_name_yomi,
      :last_name,  :last_name_yomi,
      COALESCE(:status, 'active')
    )
    """

    inserted = 0
    skipped = 0
    warned = 0

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        headers = r.fieldnames or []

        for need in ["first_name", "last_name"]:
            if need not in headers:
                raise SystemExit(f"CSV header missing '{need}'. Found: {headers}")

        for line_no, row in enumerate(r, start=2):
            first_name = norm_str(row.get("first_name"))
            last_name  = norm_str(row.get("last_name"))
            if not first_name or not last_name:
                skipped += 1
                warned += 1
                print(f"[WARN] line {line_no}: first_name/last_name required -> skipped")
                continue

            params = dict(
                first_name=first_name,
                first_name_yomi=norm_str(row.get("first_name_yomi")),
                last_name=last_name,
                last_name_yomi=norm_str(row.get("last_name_yomi")),
                status=norm_status(row.get("status")),
            )

            if args.dry_run:
                continue

            try:
                cur.execute(sql_insert, params)

                # DBが振ったIDを取得して Tコードを保存
                new_id = cur.lastrowid
                teacher_code = f"T{new_id:0{args.width}d}"
                cur.execute(
                    "UPDATE teachers SET teacher_code=?, updated_at=datetime('now') WHERE teacher_id=?",
                    (teacher_code, new_id)
                )

                inserted += 1
            except sqlite3.IntegrityError as e:
                skipped += 1
                warned += 1
                print(f"[WARN] line {line_no}: IntegrityError: {e} -> skipped")

    if not args.dry_run:
        conn.commit()
    conn.close()

    print("=== import result ===")
    print(f"inserted: {inserted}")
    print(f"skipped : {skipped}")
    print(f"warned  : {warned}")
    if args.dry_run:
        print("(dry-run: no changes written)")

if __name__ == "__main__":
    main()
