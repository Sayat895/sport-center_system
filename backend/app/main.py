import csv
import io
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

import bcrypt
import redis.asyncio as redis
import requests
from fastapi.concurrency import run_in_threadpool
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response as FastAPIResponse, StreamingResponse
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.db import SessionLocal
from app.models import Booking, Notification, Trainer, User, Workout
from app.schemas import GoogleLoginInput, LoginInput, NotificationOut, UserOut, UserUpdate, WorkoutCreate
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
        today = date.today().isoformat()
        await cache.delete(f"workouts:{today}", f"report-data:{today}", f"report-csv:v2:{today}")


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


@app.post("/api/auth/google")
async def google_login(data: GoogleLoginInput, session: DbSession):
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Вход через Google пока не настроен")
    try:
        claims = await run_in_threadpool(
            id_token.verify_oauth2_token,
            data.credential,
            GoogleRequest(),
            settings.google_client_id,
        )
    except (GoogleAuthError, ValueError, requests.exceptions.RequestException):
        raise HTTPException(status_code=401, detail="Не удалось подтвердить аккаунт Google") from None

    email = claims.get("email", "").lower()
    full_name = claims.get("name", "").strip()
    if not email or not full_name or claims.get("email_verified") is not True:
        raise HTTPException(status_code=401, detail="Google не подтвердил адрес электронной почты")

    user = await session.scalar(select(User).where(User.email == email))
    if user and user.role != "client":
        raise HTTPException(status_code=403, detail="Вход администратора через Google запрещен")
    if not user:
        user = User(
            full_name=full_name[:120],
            email=email,
            phone="",
            password_hash=bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt()).decode(),
            membership="Стандарт",
            role="client",
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            user = await session.scalar(select(User).where(User.email == email))
            if not user or user.role != "client":
                raise HTTPException(status_code=409, detail="Не удалось создать клиентский аккаунт") from None
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


async def build_report(session: AsyncSession) -> dict:
    total_bookings = await session.scalar(select(func.count(Booking.id)).where(Booking.status == "confirmed"))
    total_clients = await session.scalar(select(func.count(User.id)).where(User.role == "client"))
    total_trainers = await session.scalar(select(func.count(Trainer.id)))
    total_workouts = await session.scalar(select(func.count(Workout.id)).where(Workout.date >= date.today()))

    trainer_workouts = (
        select(
            Workout.trainer_id.label("trainer_id"),
            func.count(Workout.id).label("training_count"),
            func.coalesce(func.sum(Workout.capacity), 0).label("capacity"),
        )
        .where(Workout.date >= date.today())
        .group_by(Workout.trainer_id)
        .subquery()
    )
    trainer_bookings = (
        select(Workout.trainer_id.label("trainer_id"), func.count(Booking.id).label("booking_count"))
        .join(Workout, Workout.id == Booking.workout_id)
        .where(Workout.date >= date.today(), Booking.status == "confirmed")
        .group_by(Workout.trainer_id)
        .subquery()
    )
    trainer_rows = (
        await session.execute(
            select(
                Trainer.full_name,
                func.coalesce(trainer_workouts.c.training_count, 0),
                func.coalesce(trainer_bookings.c.booking_count, 0),
                func.coalesce(trainer_workouts.c.capacity, 0),
            )
            .outerjoin(trainer_workouts, trainer_workouts.c.trainer_id == Trainer.id)
            .outerjoin(trainer_bookings, trainer_bookings.c.trainer_id == Trainer.id)
            .order_by(func.coalesce(trainer_bookings.c.booking_count, 0).desc(), Trainer.full_name)
        )
    ).all()
    sport_workouts = (
        select(
            Workout.sport.label("sport"),
            func.count(Workout.id).label("training_count"),
            func.coalesce(func.sum(Workout.capacity), 0).label("capacity"),
        )
        .where(Workout.date >= date.today())
        .group_by(Workout.sport)
        .subquery()
    )
    sport_bookings = (
        select(Workout.sport.label("sport"), func.count(Booking.id).label("booking_count"))
        .join(Workout, Workout.id == Booking.workout_id)
        .where(Workout.date >= date.today(), Booking.status == "confirmed")
        .group_by(Workout.sport)
        .subquery()
    )
    sport_rows = (
        await session.execute(
            select(
                sport_workouts.c.sport,
                sport_workouts.c.training_count,
                func.coalesce(sport_bookings.c.booking_count, 0),
                sport_workouts.c.capacity,
            )
            .outerjoin(sport_bookings, sport_bookings.c.sport == sport_workouts.c.sport)
            .order_by(func.coalesce(sport_bookings.c.booking_count, 0).desc(), sport_workouts.c.sport)
        )
    ).all()
    workout_rows = (
        await session.execute(
            select(Workout)
            .options(selectinload(Workout.trainer), selectinload(Workout.bookings))
            .where(Workout.date >= date.today())
            .order_by(Workout.date, Workout.start_time)
        )
    ).scalars().unique().all()
    schedule = []
    for workout in workout_rows:
        booked = sum(booking.status == "confirmed" for booking in workout.bookings)
        schedule.append({
            "date": workout.date.isoformat(),
            "time": workout.start_time.strftime("%H:%M"),
            "sport": workout.sport,
            "title": workout.title,
            "trainer": workout.trainer.full_name,
            "booked": booked,
            "capacity": workout.capacity,
            "available": max(0, workout.capacity - booked),
            "difficulty": workout.difficulty,
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_bookings": total_bookings or 0,
            "total_clients": total_clients or 0,
            "total_trainers": total_trainers or 0,
            "total_workouts": total_workouts or 0,
        },
        "trainers": [
            {"name": name, "training_count": trainings, "bookings": bookings, "capacity": capacity, "available": max(0, capacity - bookings)}
            for name, trainings, bookings, capacity in trainer_rows
        ],
        "sports": [
            {"name": name, "training_count": trainings, "bookings": bookings, "capacity": capacity, "available": max(0, capacity - bookings)}
            for name, trainings, bookings, capacity in sport_rows
        ],
        "schedule": schedule,
    }


@app.get("/api/admin/report")
async def report_data(session: DbSession, _: AdminUser):
    cache_key = f"report-data:{date.today().isoformat()}"
    if cache:
        cached_report = await cache.get(cache_key)
        if cached_report:
            import json

            return json.loads(cached_report)
    report = await build_report(session)
    if cache:
        import json

        await cache.set(cache_key, json.dumps(report, ensure_ascii=False), ex=60)
    return report


def build_report_workbook(report: dict) -> bytes:
    workbook = Workbook()
    sheets = [
        ("Сводка", ["Показатель", "Значение"], [
            ["Отчет сформирован", report["generated_at"]],
            ["Активных записей", report["summary"]["total_bookings"]],
            ["Клиентов", report["summary"]["total_clients"]],
            ["Тренеров", report["summary"]["total_trainers"]],
            ["Тренировок в расписании", report["summary"]["total_workouts"]],
        ]),
        ("По тренерам", ["Тренер", "Занятий", "Записей", "Всего мест", "Свободно мест"], [
            [row["name"], row["training_count"], row["bookings"], row["capacity"], row["available"]]
            for row in report["trainers"]
        ]),
        ("Направления", ["Направление", "Занятий", "Записей", "Всего мест", "Свободно мест"], [
            [row["name"], row["training_count"], row["bookings"], row["capacity"], row["available"]]
            for row in report["sports"]
        ]),
        ("Расписание", ["Дата", "Время", "Направление", "Тренировка", "Тренер", "Записано", "Вместимость", "Свободно", "Уровень"], [
            [row["date"], row["time"], row["sport"], row["title"], row["trainer"], row["booked"], row["capacity"], row["available"], row["difficulty"]]
            for row in report["schedule"]
        ]),
    ]
    for index, (title, headers, rows) in enumerate(sheets):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = title
        sheet.append([f"SPORTCLUB · {title.upper()}"])
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        sheet["A1"].font = Font(name="Aptos Display", size=16, bold=True, color="FFFFFF")
        sheet["A1"].fill = PatternFill("solid", fgColor="172554")
        sheet["A1"].alignment = Alignment(vertical="center")
        sheet.row_dimensions[1].height = 32
        sheet.append(headers)
        for row in rows:
            sheet.append([safe_spreadsheet_value(value) for value in row])
        for cell in sheet[2]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="65A30D")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.freeze_panes = "A3"
        sheet.auto_filter.ref = f"A2:{sheet.cell(row=max(2, sheet.max_row), column=len(headers)).coordinate}"
        if rows:
            table = Table(displayName=f"ReportTable{index + 1}", ref=f"A2:{sheet.cell(row=sheet.max_row, column=len(headers)).coordinate}")
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium4", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
            sheet.add_table(table)
        for column_index, column in enumerate(sheet.columns, start=1):
            column_letter = get_column_letter(column_index)
            width = max((len(str(cell.value or "")) for cell in column), default=10)
            sheet.column_dimensions[column_letter].width = min(max(width + 3, 14), 42)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def safe_spreadsheet_value(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


@app.get("/api/admin/report.xlsx")
async def report_xlsx(session: DbSession, _: AdminUser):
    report = await build_report(session)
    return FastAPIResponse(
        content=build_report_workbook(report),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="sport-center-report.xlsx"'},
    )


@app.get("/api/admin/report.csv")
async def report_csv(session: DbSession, _: AdminUser):
    cache_key = f"report-csv:v2:{date.today().isoformat()}"
    if cache:
        cached_report = await cache.get(cache_key)
        if cached_report:
            return StreamingResponse(
                iter([cached_report]),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="sport-center-report.csv"'},
            )
    report = await build_report(session)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Раздел", "Название", "Занятий", "Записей", "Всего мест", "Свободно мест"])
    writer.writerow(["Сводка", "Активные записи", "", report["summary"]["total_bookings"], "", ""])
    writer.writerow(["Сводка", "Клиенты", "", report["summary"]["total_clients"], "", ""])
    writer.writerow(["Сводка", "Тренеры", "", report["summary"]["total_trainers"], "", ""])
    writer.writerow(["Сводка", "Занятия в расписании", report["summary"]["total_workouts"], "", "", ""])
    writer.writerows([safe_spreadsheet_value(value) for value in ["Загрузка тренеров", row["name"], row["training_count"], row["bookings"], row["capacity"], row["available"]]] for row in report["trainers"])
    writer.writerows([safe_spreadsheet_value(value) for value in ["Популярность направлений", row["name"], row["training_count"], row["bookings"], row["capacity"], row["available"]]] for row in report["sports"])
    writer.writerows([safe_spreadsheet_value(value) for value in ["Расписание", f'{row["date"]} {row["time"]} · {row["title"]} · {row["trainer"]}', 1, row["booked"], row["capacity"], row["available"]]] for row in report["schedule"])
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
