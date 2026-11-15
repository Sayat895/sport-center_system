from datetime import date, time

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class GoogleLoginInput(BaseModel):
    credential: str = Field(min_length=20, max_length=8192)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: EmailStr
    phone: str
    membership: str
    role: str


class UserUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(max_length=32)
    membership: str = Field(min_length=2, max_length=40)
    role: str = Field(pattern="^(client|admin)$")


class WorkoutCreate(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    sport: str = Field(pattern="^(Тренажерный зал|Бассейн|Бокс|Йога|Кроссфит)$")
    trainer_id: int = Field(gt=0)
    date: date
    start_time: time
    duration_minutes: int = Field(default=60, ge=15, le=240)
    capacity: int = Field(ge=1, le=100)
    difficulty: str = Field(default="Средний", pattern="^(Начальный|Средний|Продвинутый)$")

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        return value.strip()


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    message: str
    created_at: str
