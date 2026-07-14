import os
from pathlib import Path

from flask import Flask


DEFAULT_LABELS_ROOT = (
    r"W:\PRODUKTY\ADBL_Przeklejki\PRZEKLEJKI"
)


def create_app() -> Flask:
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=os.environ.get(
            "ADBL_SECRET_KEY",
            "adbl-label-printer-local",
        ),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        LABELS_ROOT=os.environ.get(
            "ADBL_LABELS_ROOT",
            DEFAULT_LABELS_ROOT,
        ),
        ETILABEL_PATH=os.environ.get(
            "ETILABEL_PATH",
            "",
        ),
        TEST_MODE=(
            os.environ.get("ADBL_TEST_MODE", "1")
            .strip()
            .lower()
            not in {"0", "false", "no", "off"}
        ),
    )

    upload_directory = (
        Path(app.instance_path) / "uploads"
    )
    upload_directory.mkdir(parents=True, exist_ok=True)

    app.config["UPLOAD_FOLDER"] = str(upload_directory)

    from app.routes import main

    app.register_blueprint(main)

    return app