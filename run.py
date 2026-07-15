from __future__ import annotations

import ctypes
import json
import multiprocessing
import os
import threading
import traceback
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from waitress import serve

from app import create_app


HOST = "127.0.0.1"
PORT = 5050
APPLICATION_URL = f"http://{HOST}:{PORT}"


def _data_directory() -> Path:
    local_app_data = os.environ.get(
        "LOCALAPPDATA",
        str(Path.home()),
    )

    directory = (
        Path(local_app_data)
        / "ADBL Label Printer"
    )

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    return directory


def _write_startup_error(
    error: BaseException,
) -> Path:
    error_path = (
        _data_directory()
        / "startup-error.log"
    )

    with error_path.open(
        "a",
        encoding="utf-8",
    ) as error_file:
        error_file.write(
            "\n"
            + "=" * 70
            + "\n"
        )

        error_file.write(
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

        error_file.write("\n")

        error_file.write(
            "".join(
                traceback.format_exception(
                    type(error),
                    error,
                    error.__traceback__,
                )
            )
        )

    return error_path


def _show_error(
    title: str,
    message: str,
) -> None:
    ctypes.windll.user32.MessageBoxW(
        0,
        message,
        title,
        0x10,
    )


def _application_is_running() -> bool:
    try:
        with urllib.request.urlopen(
            f"{APPLICATION_URL}/api/config",
            timeout=1,
        ) as response:
            if response.status != 200:
                return False

            data = json.loads(
                response.read().decode("utf-8")
            )

            return isinstance(data, dict)

    except Exception:
        return False


def _open_browser() -> None:
    webbrowser.open(APPLICATION_URL)


def main() -> None:
    if _application_is_running():
        _open_browser()
        return

    try:
        app = create_app()

        if (
            os.environ.get("ADBL_NO_BROWSER")
            != "1"
        ):
            threading.Timer(
                1.2,
                _open_browser,
            ).start()

        serve(
            app,
            host=HOST,
            port=PORT,
            threads=4,
        )

    except BaseException as exc:
        error_path = _write_startup_error(exc)

        _show_error(
            "ADBL Label Printer",
            (
                "Nie udało się uruchomić "
                "programu.\n\n"
                f"{exc}\n\n"
                "Szczegóły zapisano w:\n"
                f"{error_path}"
            ),
        )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()