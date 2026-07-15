from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from flask import Flask

from app.audit_log import setup_audit_logging


DEFAULT_LABELS_ROOT = (
    r"W:\PRODUKTY\ADBL_Przeklejki\PRZEKLEJKI"
)

DEFAULT_CONFIG = {
    "test_mode": False,
    "labels_root": DEFAULT_LABELS_ROOT,
    "etilabel_path": "",
}


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
            Path.home()
            / "AppData"
            / "Local"
            / "ADBL Label Printer"
        )

    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_config(
    config_path: Path,
    config_data: dict,
) -> None:
    config_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with config_path.open(
        "w",
        encoding="utf-8",
    ) as config_file:
        json.dump(
            config_data,
            config_file,
            ensure_ascii=False,
            indent=4,
        )


def _prepare_local_config(
    data_directory: Path,
) -> Path:
    local_config_path = (
        data_directory / "config.json"
    )

    if local_config_path.is_file():
        return local_config_path

    # Migracja dotychczasowej konfiguracji
    # znajdującej się obok kodu lub pliku EXE.
    legacy_config_path = (
        _application_directory()
        / "config.json"
    )

    if legacy_config_path.is_file():
        try:
            shutil.copy2(
                legacy_config_path,
                local_config_path,
            )
            return local_config_path
        except OSError:
            pass

    # Pierwsze uruchomienie jest zawsze bezpieczne:
    # domyślnie włączamy tryb testowy.
    _write_config(
        local_config_path,
        DEFAULT_CONFIG,
    )

    return local_config_path


def _load_json_config(
    config_path: Path,
) -> dict:
    try:
        with config_path.open(
            "r",
            encoding="utf-8",
        ) as config_file:
            data = json.load(config_file)

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Plik konfiguracji zawiera błąd "
            f"w wierszu {exc.lineno}: {exc.msg}. "
            f"Plik: {config_path}"
        ) from exc

    except OSError as exc:
        raise RuntimeError(
            "Nie udało się odczytać konfiguracji: "
            f"{config_path}. {exc}"
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

    config_path = _prepare_local_config(
        data_directory
    )

    json_config = _load_json_config(
        config_path
    )

    app = Flask(
        __name__,
        instance_path=str(
            data_directory / "instance"
        ),
        instance_relative_config=True,
    )

    test_mode_value = os.environ.get(
        "ADBL_TEST_MODE",
        json_config.get("test_mode", False),
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
        CONFIG_PATH=str(config_path),
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