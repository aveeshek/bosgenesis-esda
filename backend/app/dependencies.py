from collections.abc import Callable

from fastapi import Depends, HTTPException, Request

from backend.app.auth.security import SessionPrincipal, read_session_cookie
from backend.app.config import Settings
from backend.app.db.database import Database, RunRepository
from backend.app.logging.postgres_logger import PostgresLogger


SESSION_COOKIE_NAME = "esda_session"


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_run_repository(request: Request) -> RunRepository:
    return request.app.state.repository


def get_postgres_logger(request: Request) -> PostgresLogger:
    return request.app.state.logger


def get_current_user_or_none(request: Request) -> SessionPrincipal | None:
    settings: Settings = request.app.state.settings
    return read_session_cookie(request.cookies.get(SESSION_COOKIE_NAME), settings.secret_key)


def get_current_user(request: Request) -> SessionPrincipal:
    principal = get_current_user_or_none(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


def require_role(role: str) -> Callable[[SessionPrincipal], SessionPrincipal]:
    def dependency(principal: SessionPrincipal = Depends(get_current_user)) -> SessionPrincipal:
        if role not in principal.roles and "admin" not in principal.roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return principal

    return dependency