#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import re
import sqlite3
import pandas as pd


def normalize_name(s: str) -> str:
    """氏名のスペース（半角/全角）を消して一致させる"""
    if s is None:
        return ""
    return str(s).strip().replace("　", "").replace(" ", "")


def parse_grade_to_school_year(raw) -> int | None:
    """
    CSVの「学年」を students.school_year(1..12) に変換
    想定入力:
      - 小学6年生 / 中学2年生 / 高校1年生
      - 小6 / 中2 / 高1
      - e5 / j3 / h2
      - 1..12（通算）
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if s == "":
        return None

    s2 = s.lower().replace(" ", "").replace("　", "")

    # e5/j3/h2
    m = re.fullmatch(r"([ejh])(\d{1,2})", s2)
    if m:
        k, n = m.group(1), int(m.group(2))
        if k == "e" and 1 <= n <= 6:
            return n
        if k == "j" and 1 <= n <= 3:
            return 6 + n
        if k == "h" and 1 <= n <= 3:
            return 9 + n
        return None

    # 小学6年生 / 小6 など
    m = re.search(r"(小学|小学校|小)\s*(\d)", s)
    if m:
        n = int(m.group(2))
        return n if 1 <= n <= 6 else None

    # 中学2年生 / 中2 など
    m = re.search(r"(中学|中学校|中)\s*(\d)", s)
    if m:
        n = int(m.group(2))
        return 6 + n if 1 <= n <= 3 else None

    # 高校1年生 / 高1 など
    m = re.search(r"(高校|高等学校|高)\s*(\d)", s)
    if m:
        n = int(m.group(2))
        return 9 + n if 1 <= n <= 3 else None

    # 1..12 通算
    if re.fullmatch(r"\d{1,2}", s2):
        n = int(s2)
        return n if 1 <= n <= 12 else None

    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--encoding", default="cp932")
    ap.add_argument("--write", action="store_true", help="DBを書き換える（指定なしはdry-run）")
    ap.add_argument("--preview", type=int, default=30)
    args = ap.parse_args()

    df = pd.read_csv(args.csv, encoding=args.encoding)

    # 列固定：生徒氏名 / 学年
    if "生徒氏名" not in df.columns or "学年" not in df.columns:
        raise SystemExit(f"CSVに必要列がありません。列一覧: {list(df.columns)}")

    name_to_year: dict[str, int] = {}
    skipped = 0

    for _, r in df.iterrows():
        nm = normalize_name(r.get("生徒氏名"))
        sy = parse_grade_to_school_year(r.get("学年"))
        if nm == "" or sy is None:
            skipped += 1
            continue
        # 同名が複数行あるなら最後を採用（同姓同名問題はDB側マッチで弾く）
        name_to_year[nm] = sy

    print("=== input summary ===")
    print("rows:", len(df))
    print("usable mappings:", len(name_to_year))
    print("skipped(blank/unparseable):", skipped)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    students = conn.execute("""
        SELECT student_id, student_code, last_name, first_name, school_year
        FROM students
        WHERE status='active'
    """).fetchall()

    # DB側キー：姓+名（スペースなし）
    key_to_students: dict[str, list[sqlite3.Row]] = {}
    for s in students:
        k = normalize_name(f"{s['last_name']}{s['first_name']}")
        key_to_students.setdefault(k, []).append(s)

    to_update = []
    already = 0
    not_found = 0
    ambiguous = []

    for nm_key, new_year in name_to_year.items():
        cands = key_to_students.get(nm_key)
        if not cands:
            not_found += 1
            continue
        if len(cands) >= 2:
            ambiguous.append((nm_key, new_year, [(c["student_id"], c["student_code"]) for c in cands]))
            continue

        s = cands[0]
        old_year = s["school_year"]
        if old_year == new_year:
            already += 1
            continue

        to_update.append((s["student_id"], s["student_code"], nm_key, old_year, new_year))

    print("\n=== match summary ===")
    print("will_update:", len(to_update))
    print("already_same:", already)
    print("not_found:", not_found)
    print("ambiguous_same_name_skipped:", len(ambiguous))

    print(f"\n=== preview (first {args.preview}) ===")
    for sid, scode, nm, old, new in to_update[: args.preview]:
        print(f"{sid}\t{scode}\t{nm}\t{old} -> {new}")

    if ambiguous:
        print("\n=== ambiguous examples (first 10) ===")
        for nm, new_year, cands in ambiguous[:10]:
            print(f"{nm}\t-> {new_year}\tcandidates={cands}")

    if args.write:
        conn.execute("BEGIN;")
        conn.executemany(
            "UPDATE students SET school_year=?, updated_at=datetime('now') WHERE student_id=?",
            [(new, sid) for sid, _, _, _, new in to_update],
        )
        conn.execute("COMMIT;")
        print("\n✅ updated students.school_year")
    else:
        print("\n(dry-run) --write を付けると更新します。")

    conn.close()


if __name__ == "__main__":
    main()
