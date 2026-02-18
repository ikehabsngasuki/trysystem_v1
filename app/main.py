from fastapi import FastAPI
from .routers import health, schedule, lessons

app = FastAPI(title="塾 基幹システム MVP", version="0.1")

app.include_router(health.router)
app.include_router(schedule.router)
app.include_router(lessons.router)
