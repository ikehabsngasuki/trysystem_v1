#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sqlite3
import pandas as pd

STAGE_MARK = {"小学生": "elementary", "中学生": "junior", "高校生": "high"}


def norm(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return str(s).strip().replace("　", "").replace(" ", "")


def fetch_subject_id(conn: sqlite3.Connection, display_name: str) -> int | None:
    row = conn.execute(
        "SELECT subject_id FROM subjects WHERE display_name=? AND status='active';",
        (display_name,),
    ).fetchone()
    return int(row[0]) if row else None


def fetch_subject_id_by_group(conn: sqlite3.Connection, school_stage: str, subject_group: str) -> int | None:
    # 総合（detail NULL）に寄せる
    row = conn.execute(
        """
        SELECT subject_id
        FROM subjects
        WHERE school_stage=? AND subject_group=? AND subject_detail IS NULL AND status='active'
        """,
        (school_stage, subject_group),
    ).fetchone()
    return int(row[0]) if row else None


def fetch_teacher_code(conn: sqlite3.Connection, ln: str, fn: str, lny: str, fny: str) -> str | None:
    """
    1) 姓名 + よみ で一致
    2) ダメなら 姓名のみ で検索し、1件一致なら採用（複数は事故防止でスキップ）
    """
    # 1) 姓名 + よみ（完全一致）
    row = conn.execute(
        """
        SELECT teacher_code
        FROM teachers
        WHERE last_name=? AND first_name=?
          AND COALESCE(last_name_yomi,'')=? AND COALESCE(first_name_yomi,'')=?
        """,
        (ln, fn, lny, fny),
    ).fetchone()
    if row:
        return str(row[0])

    # 2) 姓名のみ（1件だけなら採用）
    rows = conn.execute(
        """
        SELECT teacher_code
        FROM teachers
        WHERE last_name=? AND first_name=?
        """,
        (ln, fn),
    ).fetchall()

    if len(rows) == 1:
        return str(rows[0][0])

    return None


def map_token_to_subject_ids(conn: sqlite3.Connection, stage: str, token: str) -> list[int]:
    """
    Excel内の科目文字列 -> subjects.subject_id の配列へ
    """
    t = token

    # 小中：これまで「総合（detail NULL）固定」だったが、detail も扱えるようにする
    if stage in ("elementary", "junior"):
        # 1) まず display_name 完全一致を優先（事故りにくい）
        sid = fetch_subject_id(conn, t)
        if sid:
            return [sid]

        # 2) "社会（歴史）" / "社会(歴史)" / "社会:歴史" のような group+detail 表記をパース
        grp = det = None
        if "（" in t and t.endswith("）"):
            grp, det = t.split("（", 1)
            det = det[:-1]  # remove trailing "）"
        elif "(" in t and t.endswith(")"):
            grp, det = t.split("(", 1)
            det = det[:-1]  # remove trailing ")"
        elif ":" in t:
            grp, det = t.split(":", 1)

        grp = norm(grp) if grp is not None else None
        det = norm(det) if det is not None else None

        if grp and det:
            row = conn.execute(
                """
                SELECT subject_id
                FROM subjects
                WHERE school_stage=? AND subject_group=? AND subject_detail=? AND status='active'
                """,
                (stage, grp, det),
            ).fetchone()
            if row:
                return [int(row[0])]

            # display_name が "中学生 社会（歴史）" のような場合にも当てに行く（柔軟化）
            prefix = "小学生" if stage == "elementary" else "中学生"
            sid = fetch_subject_id(conn, f"{prefix} {grp}（{det}）")
            if sid:
                return [sid]

        # 3) Excel が "歴史" だけのように detail 単体を渡してくる運用への最小対応
        #    中学は「社会（歴史/地理/公民）」を想定（あなたの現状と整合）
        if stage == "junior" and t in ("歴史", "地理", "公民"):
            row = conn.execute(
                """
                SELECT subject_id
                FROM subjects
                WHERE school_stage='junior'
                  AND subject_group='社会'
                  AND subject_detail=?
                  AND status='active'
                """,
                (t,),
            ).fetchone()
            if row:
                return [int(row[0])]

            sid = fetch_subject_id(conn, f"中学生 社会（{t}）")
            if sid:
                return [sid]

        # 4) 最後に従来互換：総合（detail NULL）へ寄せる
        sid = fetch_subject_id_by_group(conn, stage, t)
        return [sid] if sid else []

    # 高校
    # まず display_name 完全一致（例: "高校 物理" など）
    sid = fetch_subject_id(conn, f"高校 {t}")
    if sid:
        return [sid]

    # 高校 総合（数学/英語/国語/社会/古典 など）
    sid = fetch_subject_id_by_group(conn, "high", t)
    if sid:
        return [sid]

    # 数学1A/2B/3C はあなたのsubjects表記に合わせて寄せる
    if t == "数学1A":
        sid = fetch_subject_id(conn, "高校 数学IA")
        return [sid] if sid else []

    if t == "数学2B":
        sid = fetch_subject_id(conn, "高校 数学IIB")
        return [sid] if sid else []

    if t == "数学3C":
        # subjects は「高校 数学IIIC」（IIIとCが合体）になっている前提
        sid = fetch_subject_id(conn, "高校 数学IIIC")
        return [sid] if sid else []

    # 古典は「高校 古典」があればそれ、無ければ古文＋漢文に分解
    if t == "古典":
        sid = fetch_subject_id(conn, "高校 古典")
        if sid:
            return [sid]
        sids = []
        sid_kobun = fetch_subject_id(conn, "高校 古文")
        sid_kanbun = fetch_subject_id(conn, "高校 漢文")
        if sid_kobun:
            sids.append(sid_kobun)
        if sid_kanbun:
            sids.append(sid_kanbun)
        return sids

    # ここまで来たら未対応
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--preview", type=int, default=40)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON;")

    df = pd.read_excel(args.xlsx, header=None)

    to_insert: list[tuple[str, int]] = []
    unknown_teachers = 0
    unknown_teacher_rows: list[tuple[str, str, str, str]] = []
    unknown_tokens: dict[str, int] = {}

    for _, row in df.iterrows():
        ln = norm(row.iloc[0])
        lny = norm(row.iloc[1])
        fn = norm(row.iloc[2])
        fny = norm(row.iloc[3])
        if not ln and not fn:
            continue

        teacher_code = fetch_teacher_code(conn, ln, fn, lny, fny)
        if not teacher_code:
            unknown_teachers += 1
            unknown_teacher_rows.append((ln, fn, lny, fny))
            continue

        stage = None
        for cell in row.iloc[4:]:
            v = norm(cell)
            if not v:
                continue
            if v in STAGE_MARK:
                stage = STAGE_MARK[v]
                continue
            if not stage:
                # ステージが出る前に科目が来たら無視
                continue

            sids = map_token_to_subject_ids(conn, stage, v)
            if not sids:
                unknown_tokens[v] = unknown_tokens.get(v, 0) + 1
                continue
            for sid in sids:
                to_insert.append((teacher_code, sid))

    # 重複除去（xlsx内）
    to_insert = sorted(set(to_insert))

    print("=== summary ===")
    print("pairs to insert (deduped):", len(to_insert))
    print("unknown teachers (name not found in DB):", unknown_teachers)
    if unknown_tokens:
        print("unknown subject tokens (need mapping/subjects):")
        for k, c in sorted(unknown_tokens.items(), key=lambda x: (-x[1], x[0])):
            print(f"  {k}: {c}")

    if unknown_teacher_rows:
        print("\n=== unknown teacher examples (first 30) ===")
        for x in unknown_teacher_rows[:30]:
            print(x)

    print(f"\n=== preview first {args.preview} ===")
    for tc, sid in to_insert[: args.preview]:
        name = conn.execute(
            "SELECT display_name FROM subjects WHERE subject_id=?;",
            (sid,),
        ).fetchone()[0]
        print(tc, sid, name)

    if not args.write:
        print("\n(dry-run) --write を付けると teacher_subjects に INSERT OR IGNORE します。")
        return

    conn.execute("BEGIN;")
    conn.executemany(
        """
        INSERT OR IGNORE INTO teacher_subjects (teacher_code, subject_id, proficiency, is_primary, status)
        VALUES (?, ?, 3, 0, 'active');
        """,
        to_insert,
    )
    conn.execute("COMMIT;")
    conn.close()
    print("\n✅ inserted into teacher_subjects")


if __name__ == "__main__":
    main()
