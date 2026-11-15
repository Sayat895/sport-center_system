import csv
import io
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

import bcrypt
import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Booking, Notification, Trainer, User, Workout
from app.schemas import LoginInput, NotificationOut, UserOut, UserUpdate, WorkoutCreate
from app.seed import seed_database
from app.settings import settings

ALGORITHM = "HS256"
cache: redis.Redis | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global cache
    await seed_database()
    cache = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await cache.ping()
        yield
    finally:
        await cache.aclose()


app = FastAPI(title="Sport Center API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unexpected_error_handler(_, exc: Exception):
    # Keep the process alive while returning a non-sensitive error.
    import logging

    logging.getLogger("sport-center").exception("Unhandled application error", exc_info=exc)
    return Response(
        content='{"detail":"Внутренняя ошибка сервера"}',
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        media_type="application/json",
    )


async def get_session():
    async with SessionLocal() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def create_token(user: User) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": expires}, settings.jwt_secret, algorithm=ALGORITHM)


async def authenticated_user(request: Request, session: DbSession) -> User:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    try:
        payload = jwt.decode(header[7:], settings.jwt_secret, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Недействительный токен") from None
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user


CurrentUser = Annotated[User, Depends(authenticated_user)]


async def optional_user(request: Request, session: DbSession) -> User | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(header[7:], settings.jwt_secret, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    return await session.get(User, user_id)


OptionalUser = Annotated[User | None, Depends(optional_user)]


async def invalidate_caches() -> None:
    if cache:
        await cache.delete(f"workouts:{date.today().isoformat()}", f"report:{date.today().isoformat()}")


async def admin_user(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return user


AdminUser = Annotated[User, Depends(admin_user)]


@app.get("/api/health")
async def health():
    if cache is None:
        raise HTTPException(status_code=503, detail="Сервис еще запускается")
    from sqlalchemy import text

    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
    await cache.ping()
    return {"status": "ok"}


@app.post("/api/auth/login")
async def login(data: LoginInput, session: DbSession):
    user = await session.scalar(select(User).where(User.email == data.email.lower()))
    if not user or not bcrypt.checkpw(data.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    return {"access_token": create_token(user), "token_type": "bearer", "user": UserOut.model_validate(user)}


@app.get("/api/auth/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user


@app.get("/api/workouts")
async def list_workouts(session: DbSession, user: OptionalUser):
    today = date.today()
    key = f"workouts:{today.isoformat()}"
    if cache:
        cached = await cache.get(key)
        if cached:
            import json

            payload = json.loads(cached)
            for item in payload:
                item["booked_by_me"] = bool(user and user.id in item["booked_user_ids"])
                item.pop("booked_user_ids")
            return payload
    rows = (
        await session.execute(
            select(Workout)
            .options(selectinload(Workout.trainer), selectinload(Workout.bookings))
            .where(Workout.date >= today)
            .order_by(Workout.date, Workout.start_time)
        )
    ).scalars().unique().all()
    payload = [
        {
            "id": item.id,
            "title": item.title,
            "sport": item.sport,
            "trainer": item.trainer.full_name,
            "trainer_id": item.trainer_id,
            "date": item.date.isoformat(),
            "start_time": item.start_time.strftime("%H:%M"),
            "duration_minutes": item.duration_minutes,
            "capacity": item.capacity,
            "difficulty": item.difficulty,
            "booked_count": sum(booking.status == "confirmed" for booking in item.bookings),
            "booked_user_ids": [booking.user_id for booking in item.bookings if booking.status == "confirmed"],
        }
        for item in rows
    ]
    if cache:
        import json

        await cache.set(key, json.dumps(payload, ensure_ascii=False), ex=30)
    for item in payload:
        item["booked_by_me"] = bool(user and user.id in item["booked_user_ids"])
        item.pop("booked_user_ids")
    return payload


@app.post("/api/workouts/{workout_id}/book", status_code=201)
async def book_workout(workout_id: int, session: DbSession, user: CurrentUser):
    workout = await session.scalar(select(Workout).where(Workout.id == workout_id).with_for_update())
    if not workout:
        raise HTTPException(status_code=404, detail="Тренировка не найдена")
    existing = await session.scalar(
        select(Booking).where(Booking.user_id == user.id, Booking.workout_id == workout_id)
    )
    if existing and existing.status == "confirmed":
        raise HTTPException(status_code=409, detail="Вы уже записаны на эту тренировку")
    count = await session.scalar(
        select(func.count(Booking.id)).where(Booking.workout_id == workout_id, Booking.status == "confirmed")
    )
    if count >= workout.capacity:
        raise HTTPException(status_code=409, detail="Свободных мест нет")
    if existing:
        existing.status = "confirmed"
    else:
        session.add(Booking(user_id=user.id, workout_id=workout_id))
    await session.commit()
    await invalidate_caches()
    return {"message": "Вы успешно записаны на тренировку"}


@app.get("/api/bookings/mine")
async def my_bookings(session: DbSession, user: CurrentUser):
    bookings = (
        await session.execute(
            select(Booking)
            .options(selectinload(Booking.workout).selectinload(Workout.trainer))
            .where(Booking.user_id == user.id, Booking.status == "confirmed")
            .order_by(Booking.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": booking.id,
            "workout_id": booking.workout_id,
            "title": booking.workout.title,
            "sport": booking.workout.sport,
            "trainer": booking.workout.trainer.full_name,
            "date": booking.workout.date.isoformat(),
            "start_time": booking.workout.start_time.strftime("%H:%M"),
        }
        for booking in bookings
    ]


@app.delete("/api/bookings/{booking_id}")
async def cancel_booking(booking_id: int, session: DbSession, user: CurrentUser):
    booking = await session.get(Booking, booking_id)
    if not booking or (user.role != "admin" and booking.user_id != user.id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    booking.status = "cancelled"
    if user.role == "admin":
        session.add(Notification(user_id=booking.user_id, message="Администратор отменил вашу запись на тренировку."))
    await session.commit()
    await invalidate_caches()
    return {"message": "Запись отменена"}


@app.get("/api/notifications", response_model=list[NotificationOut])
async def notifications(session: DbSession, user: CurrentUser):
    items = (
        await session.scalars(
            select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(50)
        )
    ).all()
    return [{"id": item.id, "message": item.message, "created_at": item.created_at.isoformat()} for item in items]


@app.get("/api/admin/users", response_model=list[UserOut])
async def admin_users(session: DbSession, _: AdminUser):
    return (await session.scalars(select(User).order_by(User.id))).all()


@app.get("/api/admin/bookings")
async def admin_bookings(session: DbSession, _: AdminUser):
    rows = (
        await session.execute(
            select(Booking)
            .options(selectinload(Booking.user), selectinload(Booking.workout))
            .where(Booking.status == "confirmed")
            .order_by(Booking.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": item.id,
            "user_id": item.user_id,
            "user_name": item.user.full_name,
            "workout_title": item.workout.title,
            "workout_id": item.workout_id,
            "sport": item.workout.sport,
            "date": item.workout.date.isoformat(),
            "start_time": item.workout.start_time.strftime("%H:%M"),
        }
        for item in rows
    ]


@app.patch("/api/admin/users/{user_id}", response_model=UserOut)
async def update_user(user_id: int, data: UserUpdate, session: DbSession, _: AdminUser):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    for field, value in data.model_dump().items():
        setattr(user, field, value)
    await session.commit()
    await session.refresh(user)
    return user


@app.post("/api/admin/workouts", status_code=201)
async def create_workout(data: WorkoutCreate, session: DbSession, _: AdminUser):
    if not await session.get(Trainer, data.trainer_id):
        raise HTTPException(status_code=404, detail="Тренер не найден")
    workout = Workout(**data.model_dump())
    session.add(workout)
    await session.commit()
    await invalidate_caches()
    return {"message": "Тренировка добавлена", "id": workout.id}


@app.get("/api/admin/report.csv")
async def report(session: DbSession, _: AdminUser):
    cache_key = f"report:{date.today().isoformat()}"
    if cache:
        cached_report = await cache.get(cache_key)
        if cached_report:
            return StreamingResponse(
                iter([cached_report]),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="sport-center-report.csv"'},
            )
    trainer_rows = (
        await session.execute(
            select(Trainer.full_name, func.count(Booking.id))
            .join(Workout, Workout.trainer_id == Trainer.id)
            .outerjoin(Booking, (Booking.workout_id == Workout.id) & (Booking.status == "confirmed"))
            .group_by(Trainer.id, Trainer.full_name)
            .order_by(func.count(Booking.id).desc())
        )
    ).all()
    sport_rows = (
        await session.execute(
            select(Workout.sport, func.count(Booking.id))
            .outerjoin(Booking, (Booking.workout_id == Workout.id) & (Booking.status == "confirmed"))
            .group_by(Workout.sport)
            .order_by(func.count(Booking.id).desc())
        )
    ).all()
    total = await session.scalar(select(func.count(Booking.id)).where(Booking.status == "confirmed"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Раздел", "Название", "Количество"])
    writer.writerow(["Всего активных записей", "Все направления", total or 0])
    writer.writerows(["Записи по тренерам", name, count] for name, count in trainer_rows)
    writer.writerows(["Популярные направления", sport, count] for sport, count in sport_rows)
    content = "\ufeff" + output.getvalue()
    if cache:
        await cache.set(cache_key, content, ex=60)
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sport-center-report.csv"'},
    )


@app.get("/api/trainers")
async def trainers(session: DbSession, _: AdminUser):
    items = (await session.scalars(select(Trainer).order_by(Trainer.full_name))).all()
    return [{"id": item.id, "full_name": item.full_name, "specialty": item.specialty} for item in items]
