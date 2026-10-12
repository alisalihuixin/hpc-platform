from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    # Accepts either an email address or a username.
    username: str = Field(description="Email or username", examples=["admin@hpc.local"])
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    # Optional: when supplied the refresh token is revoked as well.
    refresh_token: str | None = None


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
