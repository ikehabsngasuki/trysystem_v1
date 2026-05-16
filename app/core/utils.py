from datetime import datetime, date, timedelta
from typing import Optional

def parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def week_range(week_monday: str) -> tuple[str, str]:
    d0 = parse_ymd(week_monday)
    d1 = d0 + timedelta(days=7)
    return d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d")

def extract_from_lesson_id_from_after_json(after_json: str) -> Optional[int]:
    """
    change_log.after_json は str(dict) で保存されている前提。
    例: "{'from_after': {...}, 'to_after': {...}, 'from_lesson_id': 197, 'to_lesson_id': 555}"
    ここから from_lesson_id の数値だけ抜く。
    """
    if not after_json:
        return None
    marker = "'from_lesson_id':"
    if marker not in after_json:
        return None
    try:
        tail = after_json.split(marker, 1)[1]
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        return int(digits) if digits else None
    except Exception:
        return None
