import asyncio
from datetime import date, time, timedelta

import bcrypt
from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.models import Base, Booking, Trainer, User, Workout


def password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


async def seed_database() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        if await session.scalar(select(func.count(User.id))):
            return

        admin = User(
            full_name="Администратор Спортцентра",
            email="admin@sport.kz",
            phone="+7 (700) 000-00-01",
            password_hash=password_hash("Admin123!"),
            membership="Премиум",
            role="admin",
        )
        client = User(
            full_name="Александр Клиент",
            email="client@sport.kz",
            phone="+7 (701) 111-22-33",
            password_hash=password_hash("Client123!"),
            membership="Премиум",
        )
        trainers = [
            Trainer(full_name=name, specialty=sport)
            for name, sport in [
                ("Алия Садыкова", "Йога"),
                ("Данияр Касымов", "Бокс"),
                ("Иван Петров", "Тренажерный зал"),
                ("Мария Ахметова", "Бассейн"),
                ("Тимур Нурланов", "Кроссфит"),
            ]
        ]
        session.add_all([admin, client, *trainers])
        first_names = ["Алия", "Данияр", "Аружан", "Мирас", "Диана", "Арман", "София", "Ерлан", "Амина", "Нуржан", "Мадина", "Тимур"]
        last_names = ["Садыкова", "Касымов", "Ибраева", "Омаров", "Ахметова", "Нурланов", "Ким", "Беков", "Серикова", "Толеуов", "Жумабаева", "Алиев"]
        membership_types = ["Стандарт", "Премиум", "Утренний"]
        clients = []
        for index, (first, last) in enumerate(zip(first_names, last_names), start=1):
            clients.append(
                User(
                    full_name=f"{first} {last}",
                    email=f"member{index}@sport.kz",
                    phone=f"+7 (7{index:02d}) {index:03d}-{index:02d}-{index + 10:02d}",
                    password_hash=password_hash("Client123!"),
                    membership=membership_types[(index - 1) % len(membership_types)],
                )
            )
        session.add_all(clients)
        await session.flush()

        sports = ["Тренажерный зал", "Бассейн", "Бокс", "Йога", "Кроссфит"]
        workouts = []
        for index in range(15):
            day_offset = index % 7
            sport = sports[index % len(sports)]
            workouts.append(
                Workout(
                    title=f"{sport} — {['Энергия', 'Сила', 'Баланс'][index % 3]}",
                    sport=sport,
                    trainer_id=trainers[index % len(trainers)].id,
                    date=date.today() + timedelta(days=day_offset),
                    start_time=time(hour=8 + (index % 4) * 3, minute=0),
                    duration_minutes=60,
                    capacity=12,
                    difficulty=["Начальный", "Средний", "Продвинутый"][index % 3],
                )
            )
        session.add_all(workouts)
        await session.flush()
        for index, workout in enumerate(workouts):
            for client_user in clients[index % 12 : index % 12 + 2]:
                session.add(Booking(user_id=client_user.id, workout_id=workout.id, status="confirmed"))
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed_database())
