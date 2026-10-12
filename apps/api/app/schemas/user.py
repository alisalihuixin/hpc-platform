from pydantic import BaseModel, ConfigDict, Field


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None


class UserBase(BaseModel):
    email: str = Field(examples=["admin@hpc.local"])
    username: str = Field(min_length=3, max_length=64, examples=["admin"])
    full_name: str | None = Field(default=None, max_length=128)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)
    roles: list[str] = Field(default_factory=lambda: ["user"])


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=128)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    roles: list[str] | None = None


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    roles: list[RoleRead]


class UserList(BaseModel):
    items: list[UserRead]
    total: int
