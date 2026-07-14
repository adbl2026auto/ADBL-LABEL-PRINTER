from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import win32print


DEFAULT_PRINTER_HINTS = (
    "TSC",
    "MH640",
    "MH641",
    "10.10.6.55",
)


class PrinterDetectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrinterInfo:
    name: str
    port_name: str
    driver_name: str
    status: int
    attributes: int
    score: int
    is_default: bool
    has_warning: bool
    warning: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrinterDetectionResult:
    selected: PrinterInfo | None
    matching_printers: list[PrinterInfo]
    all_printers: list[PrinterInfo]

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": (
                self.selected.to_dict()
                if self.selected
                else None
            ),
            "matching_printers": [
                printer.to_dict()
                for printer in self.matching_printers
            ],
            "all_printers": [
                printer.to_dict()
                for printer in self.all_printers
            ],
        }


def _normalize(value: str | None) -> str:
    return " ".join(
        str(value or "")
        .casefold()
        .replace("-", " ")
        .replace("_", " ")
        .split()
    )


def _printer_score(
    name: str,
    port_name: str,
    driver_name: str,
    hints: tuple[str, ...],
) -> int:
    combined = _normalize(
        f"{name} {port_name} {driver_name}"
    )

    score = 0

    for hint in hints:
        normalized_hint = _normalize(hint)

        if normalized_hint not in combined:
            continue

        if normalized_hint in {"mh640", "mh641"}:
            score += 100
        elif normalized_hint == "tsc":
            score += 50
        elif normalized_hint == "10.10.6.55":
            score += 80
        else:
            score += 20

    return score


def _printer_warning(status: int) -> tuple[bool, str | None]:
    warnings: list[str] = []

    status_flags = (
        (
            getattr(
                win32print,
                "PRINTER_STATUS_OFFLINE",
                0x00000080,
            ),
            "drukarka jest offline",
        ),
        (
            getattr(
                win32print,
                "PRINTER_STATUS_ERROR",
                0x00000002,
            ),
            "drukarka zgłasza błąd",
        ),
        (
            getattr(
                win32print,
                "PRINTER_STATUS_PAPER_OUT",
                0x00000010,
            ),
            "brak etykiet",
        ),
        (
            getattr(
                win32print,
                "PRINTER_STATUS_PAPER_JAM",
                0x00000008,
            ),
            "zacięcie materiału",
        ),
        (
            getattr(
                win32print,
                "PRINTER_STATUS_DOOR_OPEN",
                0x00400000,
            ),
            "pokrywa drukarki jest otwarta",
        ),
        (
            getattr(
                win32print,
                "PRINTER_STATUS_NOT_AVAILABLE",
                0x00001000,
            ),
            "drukarka jest niedostępna",
        ),
        (
            getattr(
                win32print,
                "PRINTER_STATUS_USER_INTERVENTION",
                0x00100000,
            ),
            "drukarka wymaga interwencji",
        ),
    )

    for flag, description in status_flags:
        if flag and status & flag:
            warnings.append(description)

    if not warnings:
        return False, None

    return True, ", ".join(warnings)


def _get_default_printer() -> str:
    try:
        return win32print.GetDefaultPrinter()
    except Exception:
        return ""


def list_windows_printers(
    hints: tuple[str, ...] = DEFAULT_PRINTER_HINTS,
) -> list[PrinterInfo]:
    flags = (
        win32print.PRINTER_ENUM_LOCAL
        | win32print.PRINTER_ENUM_CONNECTIONS
    )

    try:
        raw_printers = win32print.EnumPrinters(
            flags,
            None,
            2,
        )
    except Exception as exc:
        raise PrinterDetectionError(
            "Nie udało się pobrać listy drukarek "
            f"z Windows: {exc}"
        ) from exc

    default_printer = _normalize(
        _get_default_printer()
    )

    printers: list[PrinterInfo] = []

    for raw_printer in raw_printers:
        name = str(
            raw_printer.get("pPrinterName") or ""
        ).strip()

        if not name:
            continue

        port_name = str(
            raw_printer.get("pPortName") or ""
        ).strip()

        driver_name = str(
            raw_printer.get("pDriverName") or ""
        ).strip()

        status = int(
            raw_printer.get("Status") or 0
        )

        attributes = int(
            raw_printer.get("Attributes") or 0
        )

        has_warning, warning = _printer_warning(
            status
        )

        score = _printer_score(
            name=name,
            port_name=port_name,
            driver_name=driver_name,
            hints=hints,
        )

        printers.append(
            PrinterInfo(
                name=name,
                port_name=port_name,
                driver_name=driver_name,
                status=status,
                attributes=attributes,
                score=score,
                is_default=(
                    _normalize(name)
                    == default_printer
                ),
                has_warning=has_warning,
                warning=warning,
            )
        )

    return sorted(
        printers,
        key=lambda printer: (
            -printer.score,
            not printer.is_default,
            printer.name.casefold(),
        ),
    )


def detect_label_printer(
    hints: tuple[str, ...] = DEFAULT_PRINTER_HINTS,
) -> PrinterDetectionResult:
    all_printers = list_windows_printers(
        hints=hints
    )

    matching_printers = [
        printer
        for printer in all_printers
        if printer.score > 0
    ]

    selected = (
        matching_printers[0]
        if matching_printers
        else None
    )

    return PrinterDetectionResult(
        selected=selected,
        matching_printers=matching_printers,
        all_printers=all_printers,
    )


def require_label_printer(
    hints: tuple[str, ...] = DEFAULT_PRINTER_HINTS,
) -> PrinterInfo:
    result = detect_label_printer(hints=hints)

    if result.selected is None:
        raise PrinterDetectionError(
            "Nie znaleziono drukarki etykiet TSC "
            "MH640/MH641. Sprawdź, czy drukarka "
            "jest zainstalowana w Windows."
        )

    if result.selected.has_warning:
        raise PrinterDetectionError(
            f"Drukarka '{result.selected.name}' "
            f"nie jest gotowa: "
            f"{result.selected.warning}."
        )

    return result.selected