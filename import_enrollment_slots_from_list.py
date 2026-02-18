#!/usr/bin/env python3
import argparse
import sqlite3
from typing import Dict, Optional, List, Tuple

import pandas as pd
import unicodedata
import difflib

DAY_MAP = {"月": 1, "火": 2, "水": 3, "木": 4, "金": 5, "土": 6, "日": 7}


def norm_str(x) -> Optional[str]:
    if x is None:
        return None
    if isinstance(x, float) and pd.isna(x):
        return None
    s = str(x).strip()
    return s if s != "" else None


def norm_name(s: Optional[str]) -> Optional[str]:
    """氏名用：NFKC + 空白除去（全角/半角/タブ等）"""
    s = norm_str(s)
    if not s:
        return None
    s = unicodedata.normalize("NFKC", s)
    # 全種類の空白を削除
    s = "".join(ch for ch in s if not ch.isspace())
    return s if s else None


def ensure_tables(cur: sqlite3.Cursor):
    for t in ("students", "teachers", "enrollment_slots"):
        r = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (t,)
        ).fetchone()
        if r is None:
            raise SystemExit(f"[ERROR] table not found: {t}")


def build_students(cur: sqlite3.Cursor) -> List[Tuple[str, str]]:
    """
    返り値: [(canonical_name, student_code), ...]
    canonical_name は last+first（studentsが正）
    """
    rows = cur.execute(
        "SELECT student_code, last_name, first_name FROM students WHERE student_code IS NOT NULL;"
    ).fetchall()

    out = []
    for student_code, last_name, first_name in rows:
        canonical = f"{last_name}{first_name}"
        out.append((norm_name(canonical) or canonical, student_code))
    return out


def build_teachers_map(cur: sqlite3.Cursor) -> Dict[str, str]:
    rows = cur.execute(
        "SELECT teacher_code, last_name, first_name FROM teachers WHERE teacher_code IS NOT NULL;"
    ).fetchall()
    m = {}
    for teacher_code, last_name, first_name in rows:
        canonical = f"{last_name}{first_name}"
        k = norm_name(canonical) or canonical
        m[k] = teacher_code
    return m


def resolve_student(name_raw: str, students: List[Tuple[str, str]], threshold: float):
    """
    戻り値: (student_code or None, canonical_name or None, score, matched_mode)
    matched_mode: exact / fuzzy / no_match
    """
    name = norm_name(name_raw)
    if not name:
        return None, None, 0.0, "no_match"

    # students: [(canonical_name, student_code)]
    # 1) 完全一致
    for canonical, code in students:
        if name == canonical:
            return code, canonical, 1.0, "exact"

    # 2) 苗字（先頭2文字）で候補を絞る（短いなら1文字）
    prefix_len = 2 if len(name) >= 2 else 1
    prefix = name[:prefix_len]
    candidates = [(c, code) for (c, code) in students if c.startswith(prefix)]

    # 候補が少なすぎ/無ければ全体から探す
    pool = candidates if candidates else students

    best_c = None
    best_code = None
    best_score = 0.0
    for canonical, code in pool:
        score = difflib.SequenceMatcher(None, name, canonical).ratio()
        if score > best_score:
            best_score = score
            best_c = canonical
            best_code = code

    if best_c is None:
        return None, None, 0.0, "no_match"

    # threshold を満たせば fuzzy として返す
    if best_score >= threshold:
        return best_code, best_c, best_score, "fuzzy"

    # threshold 未満でも「候補はある」ので score を返して no_match にする
    # （呼び出し側で "auto-fix-weak" を許可するならここを採用にできる）
    return None, best_c, best_score, "no_match"


def load_existing_slot_keys(cur: sqlite3.Cursor):
    """
    enrollment_slots に既に存在するユニークキーをロード
    UNIQUE想定: (student_code, day_of_week, slot_no, subject)
    """
    rows = cur.execute(
        "SELECT student_code, day_of_week, slot_no, subject FROM enrollment_slots;"
    ).fetchall()
    return set((r[0], int(r[1]), int(r[2]), str(r[3])) for r in rows)


def main():
    ap = argparse.ArgumentParser(description="Import enrollment_slots from 授業リスト.xlsx with student name normalization.")
    ap.add_argument("--db", required=True, help="SQLite DB path (e.g. school_v1.db)")
    ap.add_argument("--xlsx", default="授業リスト.xlsx", help="xlsx path")
    ap.add_argument("--sheet", default="Sheet1", help="sheet name (default: Sheet1)")
    ap.add_argument("--dry-run", action="store_true", help="Preview only, do not write")
    ap.add_argument("--upsert", action="store_true", help="On UNIQUE conflict, update teacher_code instead of ignoring")
    ap.add_argument("--threshold", type=float, default=0.88, help="Fuzzy match threshold (default 0.88)")
    ap.add_argument("--auto-fix", action="store_true", help="Use fuzzy matched student if above threshold")
    ap.add_argument("--rewrite-xlsx", default=None, help="Output xlsx path to rewrite 氏名 into canonical names")
    args = ap.parse_args()

    df = pd.read_excel(args.xlsx, sheet_name=args.sheet)

    required_cols = ["氏名", "受講科目", "担当", "曜日", "時間"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise SystemExit(f"[ERROR] xlsx missing columns: {missing}")

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # enrollments テーブルが無い前提でも動くように（今回は enrollment_id 触らない）
    cur.execute("PRAGMA foreign_keys=OFF;")
    ensure_tables(cur)

    students = build_students(cur)                  # [(canonical, student_code)]
    teachers_map = build_teachers_map(cur)          # {canonical: teacher_code}

    # 既存ユニークキーを取得（衝突行を明示するため）
    existing_keys = load_existing_slot_keys(cur)

    if args.upsert:
        sql = """
        INSERT INTO enrollment_slots (student_code, day_of_week, slot_no, subject, teacher_code)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(student_code, day_of_week, slot_no, subject)
        DO UPDATE SET
          teacher_code = excluded.teacher_code
        """
    else:
        sql = """
        INSERT OR IGNORE INTO enrollment_slots (student_code, day_of_week, slot_no, subject, teacher_code)
        VALUES (?, ?, ?, ?, ?)
        """

    # line情報も保持
    to_insert: List[Tuple[int, Tuple[str, int, int, str, Optional[str]]]] = []  # [(line, params)]
    unknown_students = []
    suggestions = []  # (line, raw, suggestion, score)
    unknown_teachers = []
    bad_rows = []
    rewrites = []     # (row_index_in_df, raw, canonical)

    # 衝突行ログ
    conflict_db = []      # [(line, student_code, day, slot, subject)]
    conflict_batch = []   # [(line, student_code, day, slot, subject, first_line)]
    seen_batch: Dict[Tuple[str, int, int, str], int] = {}

    for idx, row in df.iterrows():
        line = idx + 2

        raw_name = norm_str(row.get("氏名"))
        subject = norm_str(row.get("受講科目"))
        teacher_raw = norm_str(row.get("担当"))
        dow = norm_str(row.get("曜日"))
        slot = row.get("時間")

        if not raw_name or not subject or not dow or pd.isna(slot):
            bad_rows.append((line, "必須列の欠損", {"氏名": raw_name, "受講科目": subject, "曜日": dow, "時間": slot}))
            continue

        if dow not in DAY_MAP:
            bad_rows.append((line, f"曜日が不正: {dow}", {}))
            continue

        try:
            slot_no = int(slot)
        except Exception:
            bad_rows.append((line, f"時間が数値でない: {slot}", {}))
            continue

        if slot_no < 1:
            bad_rows.append((line, f"時間が1未満: {slot_no}", {}))
            continue

        # student 解決
        code, canonical, score, mode = resolve_student(raw_name, students, args.threshold)
        if code is None:
            unknown_students.append((line, raw_name))
            continue

        # fuzzy だった場合の扱い
        if mode == "fuzzy":
            suggestions.append((line, raw_name, canonical, score))
            if not args.auto_fix:
                # auto-fix しないなら unknown 扱いで止める（安全寄り）
                unknown_students.append((line, raw_name))
                continue
            # auto-fixするなら、xlsx書き換え対象にする
            if canonical and norm_name(raw_name) != canonical:
                rewrites.append((idx, raw_name, canonical))

        student_code = code

        # teacher 解決（先生は見つからないなら NULL で入れる）
        teacher_code = None
        if teacher_raw:
            tkey = norm_name(teacher_raw) or teacher_raw
            teacher_code = teachers_map.get(tkey)
            if not teacher_code:
                unknown_teachers.append((line, teacher_raw))
                teacher_code = None

        day_of_week = DAY_MAP[dow]
        params = (student_code, day_of_week, slot_no, subject, teacher_code)

        # ユニーク衝突の検知（DB既存 or 今回バッチ内重複）
        ukey = (student_code, day_of_week, slot_no, subject)

        if ukey in seen_batch:
            conflict_batch.append((line, *ukey, seen_batch[ukey]))
        else:
            seen_batch[ukey] = line

        if ukey in existing_keys:
            conflict_db.append((line, *ukey))

        to_insert.append((line, params))

    print("=== preview ===")
    print(f"rows in xlsx: {len(df)}")
    print(f"to insert (after validation): {len(to_insert)}")
    print(f"unknown students: {len(unknown_students)}")
    if unknown_students:
        print("[WARN] unknown students (show up to 10)")
        for x in unknown_students[:10]:
            print("  line", x[0], "name", x[1])

    if suggestions:
        print(f"fuzzy suggestions: {len(suggestions)} (show up to 10)")
        for x in suggestions[:10]:
            print(f"  line {x[0]} raw={x[1]} -> cand={x[2]} score={x[3]:.3f}")
        if not args.auto_fix:
            print("NOTE: --auto-fix を付けない限り fuzzy は挿入しません（安全のため）。")

    # ユニーク衝突の明示
    if conflict_db:
        print(f"[INFO] UNIQUE conflict with existing DB rows: {len(conflict_db)} (show up to 20)")
        for x in conflict_db[:20]:
            print(f"  line {x[0]} key=({x[1]}, dow={x[2]}, slot={x[3]}, subject={x[4]})")
        if args.upsert:
            print("NOTE: --upsert のため、上記は INSERT ではなく UPDATE(teacher_code更新) になります。")
        else:
            print("NOTE: デフォルトのため、上記は INSERT OR IGNORE で無視される可能性があります（後述の結果で確定）。")

    if conflict_batch:
        print(f"[WARN] Duplicate keys inside this xlsx/batch: {len(conflict_batch)} (show up to 20)")
        for x in conflict_batch[:20]:
            print(f"  line {x[0]} duplicates line {x[5]} key=({x[1]}, dow={x[2]}, slot={x[3]}, subject={x[4]})")

    if unknown_teachers:
        print(f"[WARN] unknown teachers (insert as NULL): {len(unknown_teachers)} (show up to 10)")
        for x in unknown_teachers[:10]:
            print("  line", x[0], "teacher", x[1])
    if bad_rows:
        print(f"[WARN] bad rows skipped: {len(bad_rows)} (show up to 10)")
        for x in bad_rows[:10]:
            print("  line", x[0], x[1], x[2])

    print("\nPreview first 20 inserts:")
    for line, r in to_insert[:20]:
        print("  ", "line", line, r)

    if args.dry_run:
        if args.rewrite_xlsx and rewrites:
            df2 = df.copy()
            for ridx, raw, canon in rewrites:
                df2.at[ridx, "氏名"] = canon
            df2.to_excel(args.rewrite_xlsx, index=False)
            print(f"\n(dry-run) rewrote names and saved: {args.rewrite_xlsx}")
        print("\n(dry-run: no DB changes written)")
        conn.close()
        return

    inserted_lines = []
    ignored_lines = []   # OR IGNOREでrowcount==0 の行
    updated_lines = []   # upsertで衝突して更新されたと思われる行（事前衝突検知ベース）

    cur.execute("BEGIN;")
    try:
        for line, params in to_insert:
            ukey = (params[0], params[1], params[2], params[3])
            if args.upsert and ukey in existing_keys:
                updated_lines.append(line)

            cur.execute(sql, params)

            if not args.upsert:
                if cur.rowcount == 0:
                    ignored_lines.append(line)
                else:
                    inserted_lines.append(line)
            else:
                inserted_lines.append(line)

        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        conn.close()
        raise SystemExit(f"[ERROR] IntegrityError: {e}")
    except Exception:
        conn.rollback()
        conn.close()
        raise
    finally:
        conn.close()

    if args.rewrite_xlsx and rewrites:
        df2 = df.copy()
        for ridx, raw, canon in rewrites:
            df2.at[ridx, "氏名"] = canon
        df2.to_excel(args.rewrite_xlsx, index=False)
        print(f"\nSaved rewritten xlsx: {args.rewrite_xlsx}")

    print("\n=== done ===")
    print(f"attempted: {len(to_insert)}")

    if not args.upsert:
        print(f"inserted: {len(inserted_lines)}")
        print(f"ignored (likely UNIQUE conflict): {len(ignored_lines)}")
        if ignored_lines:
            print("ignored line numbers (show up to 50):", ignored_lines[:50])
    else:
        print(f"upsert attempted: {len(inserted_lines)}")
        print(f"conflict->updated (detected by existing key): {len(updated_lines)}")
        if updated_lines:
            print("updated line numbers (show up to 50):", updated_lines[:50])

    print("NOTE: duplicates are handled by OR IGNORE or UPSERT depending on option.")


if __name__ == "__main__":
    main()
