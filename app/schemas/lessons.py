from __future__ import annotations
from pydantic import BaseModel
from typing import Optional

class LessonCreate(BaseModel):
    lesson_date: str  # YYYY-MM-DD
    slot_no: int
    student_code: str
    teacher_code: Optional[str] = None
    subject_id: Optional[int] = None
    subject_text: Optional[str] = None
    status: str = "scheduled"
    source_enrollment_slot_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    note: Optional[str] = None

class RescheduleRequest(BaseModel):
    from_lesson_id: int
    to_lesson_date: str  # YYYY-MM-DD
    to_slot_no: int
    to_teacher_code: Optional[str] = None  # 未定ならNoneでも可
    to_subject_id: Optional[int] = None
    to_subject_text: Optional[int | str] = None  # 文字も来うるので広めに
    updated_by_user_id: Optional[int] = None
    note: Optional[str] = None

class RescheduleCancelRequest(BaseModel):
    to_lesson_id: int
    updated_by_user_id: Optional[int] = None
    note: Optional[str] = None
