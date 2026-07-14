from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from flask import Flask

from app.audit_log import setup_audit_logging


DEFAULT_LABELS_ROOT = (
    r"W:\PRODUKTY\ADBL_Przeklejki\PRZEKLEJKI"
)


def _application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent.parent


def _data_directory() -> Path:
    local_app_data = os.environ.get(
        "LOCALAPPDATA"
    )

    if local_app_data:
        directory = (
            Path(local_app_data)
            / "ADBL Label Printer"
        )
    else:
        directory = (
            _application_directory()
            / "application_data"
        )

    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _load_json_config() -> dict:
    config_path = (
        _application_directory()
        / "config.json"
    )

    if not config_path.is_file():
        return {}

    try:
        with config_path.open(
            "r",
            encoding="utf-8",
        ) as config_file:
            data = json.load(config_file)

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Plik config.json zawiera błąd "
            f"w wierszu {exc.lineno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "Plik config.json musi zawierać "
            "obiekt JSON."
        )

    return data


def _parse_boolean(value) -> bool:
    if isinstance(value, bool):
        return value

    return (
        str(value)
        .strip()
        .casefold()
        not in {
            "0",
            "false",
            "no",
            "off",
            "nie",
        }
    )


def create_app() -> Flask:
    data_directory = _data_directory()
    json_config = _load_json_config()

    app = Flask(
        __name__,
        instance_path=str(
            data_directory / "instance"
        ),
        instance_relative_config=True,
    )

    test_mode_value = os.environ.get(
        "ADBL_TEST_MODE",
        json_config.get("test_mode", True),
    )

    labels_root = os.environ.get(
        "ADBL_LABELS_ROOT",
        json_config.get(
            "labels_root",
            DEFAULT_LABELS_ROOT,
        ),
    )

    etilabel_path = os.environ.get(
        "ETILABEL_PATH",
        json_config.get(
            "etilabel_path",
            "",
        ),
    )

    app.config.update(
        SECRET_KEY=os.environ.get(
            "ADBL_SECRET_KEY",
            "adbl-label-printer-local",
        ),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        LABELS_ROOT=str(labels_root),
        ETILABEL_PATH=str(etilabel_path),
        TEST_MODE=_parse_boolean(
            test_mode_value
        ),
        APPLICATION_DIRECTORY=str(
            _application_directory()
        ),
        DATA_DIRECTORY=str(data_directory),
        CONFIG_PATH=str(
            _application_directory()
            / "config.json"
        ),
    )

    upload_directory = (
        data_directory / "uploads"
    )
    upload_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_directory = data_directory / "logs"
    log_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    app.config["UPLOAD_FOLDER"] = str(
        upload_directory
    )

    app.config["LOG_FOLDER"] = str(
        log_directory
    )

    app.config["LOG_PATH"] = str(
        setup_audit_logging(
            app,
            log_directory,
        )
    )

    from app.routes import main

    app.register_blueprint(main)

    return app