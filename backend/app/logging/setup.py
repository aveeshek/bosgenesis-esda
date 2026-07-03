from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(settings) -> None:
    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    _ensure_stream_handler(root, formatter, level)

    log_dir = Path(settings.log_dir)
    if settings.log_file_enabled or settings.mop_execution_debug_log_enabled:
        log_dir.mkdir(parents=True, exist_ok=True)

    if settings.log_file_enabled:
        _ensure_rotating_handler(
            root,
            handler_id="esda_file",
            path=log_dir / settings.log_file_name,
            formatter=formatter,
            level=level,
            max_bytes=settings.log_max_bytes,
            backup_count=settings.log_backup_count,
        )

    if settings.mop_execution_debug_log_enabled:
        mop_handler = _ensure_rotating_handler(
            logging.getLogger("bosgenesis_esda.mop_execution"),
            handler_id="mop_execution_file",
            path=log_dir / settings.mop_execution_debug_log_file,
            formatter=formatter,
            level=logging.DEBUG,
            max_bytes=settings.log_max_bytes,
            backup_count=settings.log_backup_count,
        )
        for logger_name in (
            "bosgenesis_esda.mop_execution",
            "bosgenesis_esda.mop_execution_agent",
        ):
            named_logger = logging.getLogger(logger_name)
            named_logger.setLevel(logging.DEBUG)
            has_handler = any(
                getattr(handler, "_esda_handler_id", None) == "mop_execution_file"
                for handler in named_logger.handlers
            )
            if not has_handler:
                named_logger.addHandler(mop_handler)


def _ensure_stream_handler(logger: logging.Logger, formatter: logging.Formatter, level: int) -> None:
    if any(getattr(handler, "_esda_handler_id", None) == "console" for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler._esda_handler_id = "console"  # type: ignore[attr-defined]
    logger.addHandler(handler)


def _ensure_rotating_handler(
    logger: logging.Logger,
    *,
    handler_id: str,
    path: Path,
    formatter: logging.Formatter,
    level: int,
    max_bytes: int,
    backup_count: int,
) -> RotatingFileHandler:
    for handler in logger.handlers:
        if getattr(handler, "_esda_handler_id", None) == handler_id:
            return handler  # type: ignore[return-value]
    handler = RotatingFileHandler(
        path,
        maxBytes=max(100_000, int(max_bytes or 10_000_000)),
        backupCount=max(1, int(backup_count or 5)),
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler._esda_handler_id = handler_id  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return handler
