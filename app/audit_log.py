from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from flask import Flask, request


LOGGER_NAME = "adbl_label_printer"
LOG_FILENAME = "application.log"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def audit_event(
    event: str,
    *,
    level: str = "info",
    **details: Any,
) -> None:
    logger = get_logger()

    record = {
        "event": event,
        **details,
    }

    message = json.dumps(
        record,
        ensure_ascii=False,
        default=str,
    )

    log_method = getattr(
        logger,
        level.casefold(),
        logger.info,
    )

    log_method(message)


def setup_audit_logging(
    app: Flask,
    log_directory: str | Path,
) -> Path:
    directory = Path(log_directory)
    directory.mkdir(parents=True, exist_ok=True)

    log_path = directory / LOG_FILENAME
    logger = get_logger()
    logger.setLevel(logging.INFO)
    logger.propagate = False

    already_configured = any(
        getattr(handler, "_adbl_handler", False)
        for handler in logger.handlers
    )

    if not already_configured:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )

        file_handler._adbl_handler = True

        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | "
                "%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

        logger.addHandler(file_handler)

    audit_event(
        "application_started",
        test_mode=bool(app.config["TEST_MODE"]),
        labels_root=app.config["LABELS_ROOT"],
        etilabel_path=app.config["ETILABEL_PATH"],
    )

    @app.after_request
    def log_operator_action(response):
        if (
            request.method != "GET"
            and request.path.startswith("/api/")
        ):
            uploaded_file = request.files.get("file")

            audit_event(
                "operator_action",
                method=request.method,
                path=request.path,
                status_code=response.status_code,
                uploaded_filename=(
                    uploaded_file.filename
                    if uploaded_file
                    else None
                ),
            )

        return response

    return log_path