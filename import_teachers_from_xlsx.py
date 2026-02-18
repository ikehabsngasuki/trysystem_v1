#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd


@dataclass(frozen=True)
class TeacherNameKey:
    last_name: str
    first_name: str


@dataclass(frozen=True)
class TeacherRow:
    last_name: str
    first_name: str
    last_name_kana: str
    first_name_kana: str


def norm(s) -> str:
    if s is None:
        return ""
    return str(s).strip().replace("　", "").replace(" ", "")


def get_teachers_columns(conn: sqlite3.Connection) -> List[dict]:
    rows = conn.execute("PRAGMA table_info(teachers);").fetchall()
    # row: (cid, name, type, notnull, dflt_value, pk)
    cols = []
    for r in rows:
        cols.append(
            {
                "cid": r[0],
                "name": r[1],
                "type": r[2],
                "notnull": int(r[3]),
                "dflt": r[4],
                "pk": int(r[5]),
            }
        )
    return cols


def get_existing_teachers(conn: sqlite3.Connection) -> Dict[TeacherNameKey, str]:
    """
    既存講師を (姓+名) キーで保持（よみは判定に使わない）
    値は teacher_code
    """
    cols = {c["name"] for c in get_teachers_columns(conn)}
    need = {"teacher_code", "last_name", "first_name"}
    if not need.issubset(cols):
        raise SystemExit(f"teachersテーブルに必要列がありません: 必須={need}, 実際={sorted(cols)}")

    q = "SELECT teacher_code, last_name, first_name FROM teachers;"

    d: Dict[TeacherNameKey, str] = {}
    for row in conn.execute(q).fetchall():
        code, ln, fn = row
        key = TeacherNameKey(norm(ln), norm(fn))
        d[key] = code
    return d


def parse_max_teacher_code(existing_codes: List[str]) -> int:
    mx = 0
    for c in existing_codes:
        if not c:
            continue
        m = re.fullmatch(r"T(\d+)", c.strip())
        if not m:
            continue
        mx = max(mx, int(m.group(1)))
    return mx


def read_teachers_xlsx(xlsx_path: str) -> List[TeacherRow]:
    """
    teachers.xlsx はヘッダ無し想定：
      0: 姓
      1: せい(かな)
      2: 名
      3: めい(かな)
    """
    df = pd.read_excel(xlsx_path, header=None)

    teachers: List[TeacherRow] = []
    for _, r in df.iterrows():
        ln = norm(r.iloc[0] if len(r) > 0 else "")
        lnk = norm(r.iloc[1] if len(r) > 1 else "")
        fn = norm(r.iloc[2] if len(r) > 2 else "")
        fnk = norm(r.iloc[3] if len(r) > 3 else "")

        # 空行スキップ
        if not ln and not fn:
            continue

        # かなが空でも一旦許容（ただし既存比較が弱くなる）
        teachers.append(TeacherRow(ln, fn, lnk, fnk))

    # 重複除去（xlsx内）※ 姓+名 だけで重複扱い（よみは見ない）
    seen = set()
    uniq: List[TeacherRow] = []
    for t in teachers:
        k = TeacherNameKey(t.last_name, t.first_name)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(t)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--write", action="store_true", help="DBに書き込む（指定なしはdry-run）")
    ap.add_argument("--preview", type=int, default=30)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON;")

    cols = {c["name"] for c in get_teachers_columns(conn)}
    existing = get_existing_teachers(conn)
    max_no = parse_max_teacher_code(list(existing.values()))

    xlsx_teachers = read_teachers_xlsx(args.xlsx)

    to_insert: List[Tuple[str, TeacherRow]] = []
    skipped = 0
    for t in xlsx_teachers:
        name_key = TeacherNameKey(t.last_name, t.first_name)
        if name_key in existing:
            skipped += 1
            continue
        max_no += 1
        code = f"T{max_no:04d}"
        to_insert.append((code, t))

    print("=== summary ===")
    print("xlsx teachers:", len(xlsx_teachers))
    print("already exists skipped:", skipped)
    print("to insert:", len(to_insert))
    print("next teacher_code end:", f"T{max_no:04d}" if to_insert else "(no change)")

    print(f"\n=== preview first {args.preview} inserts ===")
    for code, t in to_insert[: args.preview]:
        print(code, t.last_name, t.first_name, t.last_name_kana, t.first_name_kana)

    if not args.write:
        print("\n(dry-run) --write を付けるとINSERTします。")
        return

    # INSERT（テーブルの列に合わせて柔軟に）
    # 必須：teacher_code, last_name, first_name
    insert_cols = ["teacher_code", "last_name", "first_name"]
    if "last_name_yomi" in cols:
        insert_cols.append("last_name_yomi")
    if "first_name_yomi" in cols:
        insert_cols.append("first_name_yomi")
    if "status" in cols:
        insert_cols.append("status")

    placeholders = ",".join(["?"] * len(insert_cols))
    sql = f"INSERT INTO teachers ({','.join(insert_cols)}) VALUES ({placeholders});"

    params = []
    for code, t in to_insert:
        row = [code, t.last_name, t.first_name]
        if "last_name_yomi" in cols:
            row.append(t.last_name_kana)   # 中身は「よみ」
        if "first_name_yomi" in cols:
            row.append(t.first_name_kana)  # 中身は「よみ」
        if "status" in cols:
            row.append("active")
        params.append(tuple(row))

    conn.execute("BEGIN;")
    conn.executemany(sql, params)
    conn.execute("COMMIT;")
    conn.close()

    print("\n✅ inserted into teachers")


if __name__ == "__main__":
    main()
