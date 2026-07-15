from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
)

from app.etilabel_controller import (
    EtilabelController,
)
from app.label_catalog import build_print_plan
from app.order_reader import read_order
from app.printer_detector import (
    PrinterDetectionError,
    detect_label_printer,
    require_label_printer,
)
from app.print_session import PrintSession


main = Blueprint("main", __name__)

_state_lock = threading.RLock()

_current_plan = None
_current_session: PrintSession | None = None
_current_order_path: Path | None = None
_worker_thread: threading.Thread | None = None
_last_error: str | None = None


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value

    if is_dataclass(value):
        return {
            key: _serialize(item)
            for key, item in asdict(value).items()
        }

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {
            str(key): _serialize(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _serialize(item)
            for item in value
        ]

    return value


def _job_to_dict(job) -> dict[str, Any]:
    return {
        "product": getattr(
            job,
            "product_folder_name",
            "",
        ),
        "quantity": getattr(
            job,
            "quantity",
            0,
        ),
        "format": getattr(
            job,
            "label_format",
            "",
        ),
        "label_path": str(
            getattr(job, "label_path", "")
        ),
        "order_product": getattr(
            getattr(job, "item", None),
            "product_name",
            "",
        ),
    }


def _skipped_to_dict(
    skipped,
) -> dict[str, Any]:
    item = getattr(skipped, "item", None)

    return {
        "product": getattr(
            item,
            "product_name",
            "",
        ),
        "quantity": getattr(
            item,
            "quantity",
            0,
        ),
        "reason": getattr(
            skipped,
            "reason",
            (
                "Brak folderu produktu – "
                "etykieta nie jest wymagana."
            ),
        ),
    }


def _invalid_to_dict(
    invalid,
) -> dict[str, Any]:
    item = getattr(invalid, "item", None)

    return {
        "product": getattr(
            item,
            "product_name",
            "",
        ),
        "quantity": getattr(
            item,
            "quantity",
            0,
        ),
        "error": str(
            getattr(
                invalid,
                "error",
                "Nieznany błąd",
            )
        ),
    }


def _plan_to_dict(plan) -> dict[str, Any]:
    jobs_45 = [
        _job_to_dict(job)
        for job in plan.jobs_45x45
    ]

    jobs_110 = [
        _job_to_dict(job)
        for job in plan.jobs_45x110
    ]

    skipped = [
        _skipped_to_dict(item)
        for item in plan.skipped_items
    ]

    invalid = [
        _invalid_to_dict(item)
        for item in plan.invalid_items
    ]

    return {
        "country": plan.country,
        "country_folder": str(
            plan.country_folder
        ),
        "jobs_45x45": jobs_45,
        "jobs_45x110": jobs_110,
        "total_45x45": plan.total_45x45,
        "total_45x110": plan.total_45x110,
        "total_labels": (
            plan.total_45x45
            + plan.total_45x110
        ),
        "job_count": (
            len(jobs_45)
            + len(jobs_110)
        ),
        "skipped_items": skipped,
        "invalid_items": invalid,
        "invalid_count": len(invalid),
        "has_warnings": bool(invalid),
        "can_print": bool(
            jobs_45 or jobs_110
        ),
    }


def _find_etilabel() -> Path:
    configured_path = current_app.config.get(
        "ETILABEL_PATH",
        "",
    )

    candidates: list[Path] = []

    if configured_path:
        candidates.append(
            Path(configured_path)
        )

    candidates.extend(
        [
            Path(
                r"C:\Etilabel\Etilabel.exe"
            ),
            Path(
                r"C:\Program Files"
                r"\Etilabel\Etilabel.exe"
            ),
            Path(
                r"C:\Program Files (x86)"
                r"\Etilabel\Etilabel.exe"
            ),
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise RuntimeError(
        "Nie znaleziono programu ETILABEL. "
        "Ustaw zmienną ETILABEL_PATH albo "
        "zainstaluj program w C:\\Etilabel."
    )


def _printer_snapshot() -> dict[str, Any]:
    try:
        result = detect_label_printer()

        return {
            "ok": True,
            "found": (
                result.selected is not None
            ),
            "selected": (
                result.selected.to_dict()
                if result.selected
                else None
            ),
            "matching_printers": [
                printer.to_dict()
                for printer
                in result.matching_printers
            ],
            "all_printers": [
                printer.to_dict()
                for printer
                in result.all_printers
            ],
            "error": None,
        }

    except Exception as exc:
        return {
            "ok": False,
            "found": False,
            "selected": None,
            "matching_printers": [],
            "all_printers": [],
            "error": str(exc),
        }


def _require_printer_for_production() -> None:
    if current_app.config["TEST_MODE"]:
        return

    printer = require_label_printer()

    if printer.has_warning:
        raise PrinterDetectionError(
            f"Drukarka '{printer.name}' "
            f"nie jest gotowa: "
            f"{printer.warning}."
        )


def _session_snapshot() -> dict[str, Any]:
    with _state_lock:
        session = _current_session
        error = _last_error
        worker = _worker_thread

    if session is None:
        return {
            "status": "no_order",
            "error": error,
            "worker_running": False,
            "test_mode": bool(
                current_app.config[
                    "TEST_MODE"
                ]
            ),
        }

    snapshot = _serialize(
        session.snapshot()
    )

    snapshot["error"] = (
        snapshot.get("error") or error
    )

    snapshot["worker_running"] = bool(
        worker and worker.is_alive()
    )

    snapshot["test_mode"] = bool(
        current_app.config["TEST_MODE"]
    )

    return snapshot


def _run_worker(action: str) -> None:
    global _last_error

    with _state_lock:
        session = _current_session

    if session is None:
        with _state_lock:
            _last_error = (
                "Nie przygotowano zamówienia."
            )
        return

    try:
        if action == "45x45":
            session.print_45x45()

        elif action == "45x110":
            session.print_45x110()

        elif action == "retry":
            session.retry_failed_job()

        else:
            raise RuntimeError(
                f"Nieznana operacja: "
                f"{action}"
            )

        with _state_lock:
            _last_error = None

    except Exception as exc:
        with _state_lock:
            _last_error = str(exc)


def _start_worker(action: str) -> None:
    global _worker_thread
    global _last_error

    with _state_lock:
        if (
            _worker_thread is not None
            and _worker_thread.is_alive()
        ):
            raise RuntimeError(
                "Drukowanie już trwa."
            )

        _last_error = None

        _worker_thread = threading.Thread(
            target=_run_worker,
            args=(action,),
            name=f"label-print-{action}",
            daemon=True,
        )

        _worker_thread.start()


def _safe_uploaded_filename(
    uploaded_name: str,
) -> str:
    normalized_name = (
        uploaded_name.replace(
            "\\",
            "/",
        )
    )

    filename = Path(
        normalized_name
    ).name

    if not filename:
        raise RuntimeError(
            "Nieprawidłowa nazwa pliku."
        )

    return filename


def _delete_uploaded_file(
    file_path: Path | None,
) -> None:
    if file_path is None:
        return

    try:
        file_path.unlink(
            missing_ok=True
        )
    except OSError:
        pass


@main.get("/")
def index():
    return render_template(
        "index.html"
    )


@main.get("/api/config")
def get_config():
    try:
        etilabel_path = str(
            _find_etilabel()
        )
        etilabel_found = True

    except RuntimeError:
        etilabel_path = ""
        etilabel_found = False

    return jsonify(
        {
            "ok": True,
            "labels_root": (
                current_app.config[
                    "LABELS_ROOT"
                ]
            ),
            "etilabel_found": (
                etilabel_found
            ),
            "etilabel_path": (
                etilabel_path
            ),
            "test_mode": bool(
                current_app.config[
                    "TEST_MODE"
                ]
            ),
            "printer": (
                _printer_snapshot()
            ),
        }
    )


@main.get("/api/printer")
def get_printer():
    return jsonify(
        {
            "ok": True,
            "test_mode": bool(
                current_app.config[
                    "TEST_MODE"
                ]
            ),
            "printer": (
                _printer_snapshot()
            ),
        }
    )


@main.post("/api/analyze")
def analyze_order():
    global _current_plan
    global _current_session
    global _current_order_path
    global _last_error

    uploaded_file = request.files.get(
        "file"
    )

    if uploaded_file is None:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Nie wybrano pliku."
                ),
            }
        ), 400

    if not uploaded_file.filename:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Plik nie ma nazwy."
                ),
            }
        ), 400

    extension = Path(
        uploaded_file.filename
    ).suffix.lower()

    if extension not in {
        ".xlsx",
        ".xlsm",
    }:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Obsługiwane są tylko "
                    "pliki .xlsx oraz .xlsm."
                ),
            }
        ), 400

    destination: Path | None = None

    try:
        original_filename = (
            _safe_uploaded_filename(
                uploaded_file.filename
            )
        )

        unique_name = (
            f"{uuid.uuid4().hex}_"
            f"{original_filename}"
        )

        destination = (
            Path(
                current_app.config[
                    "UPLOAD_FOLDER"
                ]
            )
            / unique_name
        )

        uploaded_file.save(
            destination
        )

        order = read_order(
            destination
        )

        plan = build_print_plan(
            order,
            current_app.config[
                "LABELS_ROOT"
            ],
        )

        plan_data = _plan_to_dict(
            plan
        )

        # Błędne pozycje nie blokują całego
        # zamówienia. Zostaną pokazane jako
        # ostrzeżenia i pominięte przy druku.
        #
        # Druk blokujemy wyłącznie wtedy,
        # gdy nie ma ani jednego poprawnego
        # zadania ETX.
        if not plan_data["can_print"]:
            _delete_uploaded_file(
                destination
            )

            return jsonify(
                {
                    "ok": False,
                    "error": (
                        "W zamówieniu nie "
                        "znaleziono żadnej "
                        "prawidłowej etykiety "
                        "do wydrukowania."
                    ),
                    "plan": plan_data,
                }
            ), 422

        etilabel_path = (
            _find_etilabel()
        )

        controller = (
            EtilabelController(
                etilabel_path
            )
        )

        session = PrintSession(
            plan,
            controller,
            test_mode=bool(
                current_app.config[
                    "TEST_MODE"
                ]
            ),
        )

        with _state_lock:
            old_order_path = (
                _current_order_path
            )

            _current_plan = plan
            _current_session = session
            _current_order_path = (
                destination
            )
            _last_error = None

        if (
            old_order_path
            and old_order_path
            != destination
        ):
            _delete_uploaded_file(
                old_order_path
            )

        warning_count = len(
            plan.invalid_items
        )

        if warning_count:
            response_message = (
                "Zamówienie przeanalizowano. "
                f"{warning_count} pozycji "
                "zostanie pominiętych z powodu "
                "błędów. Pozostałe etykiety "
                "można wydrukować."
            )
        else:
            response_message = (
                "Zamówienie zostało poprawnie "
                "przeanalizowane."
            )

        return jsonify(
            {
                "ok": True,
                "filename": (
                    uploaded_file.filename
                ),
                "message": response_message,
                "has_warnings": bool(
                    warning_count
                ),
                "warning_count": (
                    warning_count
                ),
                "plan": plan_data,
                "session": (
                    _session_snapshot()
                ),
                "printer": (
                    _printer_snapshot()
                ),
            }
        )

    except Exception as exc:
        _delete_uploaded_file(
            destination
        )

        return jsonify(
            {
                "ok": False,
                "error": str(exc),
            }
        ), 400


@main.post("/api/print/45x45")
def print_45x45():
    with _state_lock:
        session = _current_session

    if session is None:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Najpierw wgraj i "
                    "przeanalizuj zamówienie."
                ),
            }
        ), 400

    try:
        _require_printer_for_production()
        _start_worker("45x45")

    except (
        RuntimeError,
        PrinterDetectionError,
    ) as exc:
        return jsonify(
            {
                "ok": False,
                "error": str(exc),
            }
        ), 409

    return jsonify(
        {
            "ok": True,
            "message": (
                "Rozpoczęto druk "
                "etykiet 45x45."
            ),
        }
    ), 202


@main.post("/api/print/45x110")
def print_45x110():
    with _state_lock:
        session = _current_session

    if session is None:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Najpierw wgraj i "
                    "przeanalizuj zamówienie."
                ),
            }
        ), 400

    snapshot = _serialize(
        session.snapshot()
    )

    if (
        snapshot.get("status")
        != "waiting_for_110"
    ):
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Druk 45x110 można "
                    "rozpocząć dopiero po "
                    "zakończeniu etapu 45x45."
                ),
                "status": snapshot.get(
                    "status"
                ),
            }
        ), 409

    try:
        _require_printer_for_production()
        _start_worker("45x110")

    except (
        RuntimeError,
        PrinterDetectionError,
    ) as exc:
        return jsonify(
            {
                "ok": False,
                "error": str(exc),
            }
        ), 409

    return jsonify(
        {
            "ok": True,
            "message": (
                "Rozpoczęto druk "
                "etykiet 45x110."
            ),
        }
    ), 202


@main.post("/api/print/retry")
def retry_failed_print():
    with _state_lock:
        session = _current_session

    if session is None:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Nie ma aktywnego "
                    "zamówienia."
                ),
            }
        ), 400

    snapshot = _serialize(
        session.snapshot()
    )

    if (
        snapshot.get("status")
        != "failed"
    ):
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Brak błędnej etykiety "
                    "do ponowienia."
                ),
            }
        ), 409

    try:
        _require_printer_for_production()
        _start_worker("retry")

    except (
        RuntimeError,
        PrinterDetectionError,
    ) as exc:
        return jsonify(
            {
                "ok": False,
                "error": str(exc),
            }
        ), 409

    return jsonify(
        {
            "ok": True,
            "message": (
                "Ponowiono drukowanie od "
                "błędnej etykiety."
            ),
        }
    ), 202


@main.post("/api/print/abort")
def abort_print():
    with _state_lock:
        session = _current_session

    if session is None:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Nie ma aktywnego "
                    "zamówienia."
                ),
            }
        ), 400

    try:
        session.abort()

    except RuntimeError as exc:
        return jsonify(
            {
                "ok": False,
                "error": str(exc),
            }
        ), 409

    return jsonify(
        {
            "ok": True,
            "message": (
                "Zamówienie zostało "
                "przerwane."
            ),
            "session": (
                _session_snapshot()
            ),
        }
    )


@main.get("/api/status")
def get_status():
    with _state_lock:
        plan = _current_plan

    response = {
        "ok": True,
        "session": (
            _session_snapshot()
        ),
    }

    if plan is not None:
        response["plan"] = (
            _plan_to_dict(plan)
        )

    return jsonify(response)


@main.post("/api/reset")
def reset_session():
    global _current_plan
    global _current_session
    global _current_order_path
    global _last_error

    with _state_lock:
        if (
            _worker_thread is not None
            and _worker_thread.is_alive()
        ):
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        "Nie można wyczyścić "
                        "sesji podczas "
                        "drukowania."
                    ),
                }
            ), 409

        old_order_path = (
            _current_order_path
        )

        _current_plan = None
        _current_session = None
        _current_order_path = None
        _last_error = None

    _delete_uploaded_file(
        old_order_path
    )

    return jsonify(
        {"ok": True}
    )