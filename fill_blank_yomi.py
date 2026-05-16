#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd


def norm(s) -> str:
    if s is None:
        return ""
    return str(s).strip().replace("　", "").replace(" ", "")


@dataclass(frozen=True)
class NameKey:
    last_name: str
    first_name: str


@dataclass(frozen=True)
class XlsxRow:
    last_name: str
    first_name: str
    last_name_yomi: str
    first_name_yomi: str


def get_teachers_columns(conn: sqlite3.Connection) -> set:
    rows = conn.execute("PRAGMA table_info(teachers);").fetchall()
    return {r[1] for r in rows}


def read_teachers_xlsx(xlsx_path: str) -> List[XlsxRow]:
    """
    teachers.xlsx はヘッダ無し想定：
      0: 姓
      1: せい(かな)  -> last_name_yomi
      2: 名
      3: めい(かな)  -> first_name_yomi
    """
    df = pd.read_excel(xlsx_path, header=None)

    out: List[XlsxRow] = []
    for _, r in df.iterrows():
        ln = norm(r.iloc[0] if len(r) > 0 else "")
        lny = norm(r.iloc[1] if len(r) > 1 else "")
        fn = norm(r.iloc[2] if len(r) > 2 else "")
        fny = norm(r.iloc[3] if len(r) > 3 else "")

        if not ln and not fn:
            continue

        out.append(XlsxRow(ln, fn, lny, fny))

    # xlsx内重複は「姓+名」で後勝ち（最後の行を採用）
    # 既存ロジックに寄せるなら先勝ちでも良いが、よみ補完は後勝ちの方が自然なのでこのまま
    d: Dict[NameKey, XlsxRow] = {}
    for r in out:
        d[NameKey(r.last_name, r.first_name)] = r

    return list(d.values())


def build_db_index(conn: sqlite3.Connection) -> Dict[NameKey, List[Tuple[int, str, str]]]:
    """
    姓名 -> [(teacher_id, last_name_yomi, first_name_yomi), ...]
    同姓同名が複数いる可能性があるのでリストで持つ
    """
    q = """
    SELECT teacher_id, last_name, first_name,
           COALESCE(last_name_yomi,''), COALESCE(first_name_yomi,'')
    FROM teachers;
    """
    idx: Dict[NameKey, List[Tuple[int, str, str]]] = {}
    for teacher_id, ln, fn, lny, fny in conn.execute(q).fetchall():
        k = NameKey(norm(ln), norm(fn))
        idx.setdefault(k, []).append((int(teacher_id), norm(lny), norm(fny)))
    return idx


def is_empty(s: str) -> bool:
    return norm(s) == ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--write", action="store_true", help="DBに書き込む（指定なしはdry-run）")
    ap.add_argument("--preview", type=int, default=30)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON;")

    cols = get_teachers_columns(conn)
    need = {"teacher_id", "last_name", "first_name", "last_name_yomi", "first_name_yomi"}
    if not need.issubset(cols):
        raise SystemExit(f"teachersテーブルに必要列がありません: 必須={need}, 実際={sorted(cols)}")

    xlsx_rows = read_teachers_xlsx(args.xlsx)
    db_idx = build_db_index(conn)

    to_update: List[Tuple[int, str, str, str, str]] = []
    unknown_in_db = 0
    ambiguous = 0
    no_yomi_in_xlsx = 0

    for xr in xlsx_rows:
        k = NameKey(xr.last_name, xr.first_name)
        matches = db_idx.get(k)
        if not matches:
            unknown_in_db += 1
            continue
        if len(matches) >= 2:
            ambiguous += 1
            continue

        teacher_id, db_lny, db_fny = matches[0]

        # xlsx側のよみが両方空なら、更新しても意味がない
        if is_empty(xr.last_name_yomi) and is_empty(xr.first_name_yomi):
            no_yomi_in_xlsx += 1
            continue

        new_lny = db_lny if not is_empty(db_lny) else xr.last_name_yomi
        new_fny = db_fny if not is_empty(db_fny) else xr.first_name_yomi

        # DB側が両方埋まってるなら何もしない
        if new_lny == db_lny and new_fny == db_fny:
            continue

        to_update.append((teacher_id, new_lny, new_fny, db_lny, db_fny))

    print("=== summary ===")
    print("xlsx unique by name:", len(xlsx_rows))
    print("to update:", len(to_update))
    print("unknown in DB (no matching name):", unknown_in_db)
    print("ambiguous in DB (same name multiple rows):", ambiguous)
    print("xlsx has no yomi (both empty):", no_yomi_in_xlsx)

    print(f"\n=== preview first {args.preview} updates ===")
    for teacher_id, new_lny, new_fny, old_lny, old_fny in to_update[: args.preview]:
        print(f"teacher_id={teacher_id}  last_yomi: '{old_lny}' -> '{new_lny}'   first_yomi: '{old_fny}' -> '{new_fny}'")

    if not args.write:
        print("\n(dry-run) --write を付けるとUPDATEします。")
        conn.close()
        return

    conn.execute("BEGIN;")
    conn.executemany(
        "UPDATE teachers SET last_name_yomi=?, first_name_yomi=? WHERE teacher_id=?;",
        [(new_lny, new_fny, teacher_id) for (teacher_id, new_lny, new_fny, _, _) in to_update],
    )
    conn.execute("COMMIT;")
    conn.close()

    print("\n✅ updated teachers yomi")


if __name__ == "__main__":
    main()
