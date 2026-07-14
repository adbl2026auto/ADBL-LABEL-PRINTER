from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from pywinauto import Desktop, findwindows


class EtilabelError(Exception):
    """Błąd podczas sterowania programem ETILABEL."""


@dataclass(frozen=True)
class PrintResult:
    label_path: Path
    quantity: int
    printed: bool
    message: str


class EtilabelController:
    def __init__(
        self,
        executable_path: str | Path,
        window_timeout: int = 30,
        print_timeout: int = 30,
    ):
        self.executable_path = Path(executable_path)
        self.window_timeout = window_timeout
        self.print_timeout = print_timeout

        if not self.executable_path.is_file():
            raise EtilabelError(
                "Nie znaleziono programu ETILABEL: "
                f"{self.executable_path}"
            )

    def print_label(
        self,
        label_path: str | Path,
        quantity: int,
        test_mode: bool = True,
    ) -> PrintResult:
        label = Path(label_path)

        if not label.is_file():
            raise EtilabelError(
                f"Nie znaleziono etykiety: {label}"
            )

        if quantity <= 0:
            raise EtilabelError(
                "Liczba etykiet musi być większa od zera."
            )

        if self._visible_windows("TNewMainForm"):
            raise EtilabelError(
                "ETILABEL jest już otwarty. "
                "Zamknij go przed rozpoczęciem drukowania."
            )

        launched_process = subprocess.Popen(
            [
                str(self.executable_path),
                str(label),
            ]
        )

        main_window = None
        print_dialog = None

        try:
            main_window = self._wait_for_window(
                class_name="TNewMainForm",
                timeout=self.window_timeout,
            )

            self._wait_for_label_loaded(
                main_window=main_window,
                label=label,
            )

            print_dialog = (
                self._open_print_dialog(
                    main_window
                )
            )

            quantity_field, copies_field = (
                self._wait_for_quantity_fields(
                    print_dialog
                )
            )

            quantity_field.set_edit_text(
                str(quantity)
            )
            copies_field.set_edit_text("1")

            if test_mode:
                self._cancel_print_dialog(
                    print_dialog
                )
                print_dialog = None

                return PrintResult(
                    label_path=label,
                    quantity=quantity,
                    printed=False,
                    message=(
                        "Tryb testowy: ETILABEL został "
                        "otwarty, a ilość ustawiona. "
                        "Drukowanie nie zostało uruchomione."
                    ),
                )

            print_button = print_dialog.child_window(
                title="Drukuj",
                class_name="TButton",
            )

            if not print_button.exists():
                raise EtilabelError(
                    "Nie znaleziono przycisku Drukuj."
                )

            print_dialog.set_focus()
            time.sleep(0.5)
            print_button.click_input()

            self._wait_for_print_completion(
                print_dialog
            )
            print_dialog = None

            return PrintResult(
                label_path=label,
                quantity=quantity,
                printed=True,
                message=(
                    "Zlecenie zostało przekazane "
                    "do ETILABEL."
                ),
            )

        finally:
            self._cleanup(
                main_window=main_window,
                print_dialog=print_dialog,
                launched_process=launched_process,
            )

    def _wait_for_label_loaded(
        self,
        main_window,
        label: Path,
    ) -> None:
        deadline = (
            time.time() + self.window_timeout
        )
        expected_name = label.name.casefold()

        while time.time() < deadline:
            title = (
                main_window.window_text()
                .casefold()
            )

            if expected_name in title:
                # Tytuł zmienia się nieco wcześniej niż
                # kończy się ładowanie wszystkich kontrolekrolek.
                time.sleep(1.5)
                return

            time.sleep(0.25)

        raise EtilabelError(
            "ETILABEL został uruchomiony, ale "
            f"nie załadował etykiety {label.name!r}."
        )

    def _open_print_dialog(
        self,
        main_window,
    ):
        attempts = 3

        for attempt in range(
            1,
            attempts + 1,
        ):
            main_window.set_focus()
            time.sleep(0.5)
            main_window.type_keys("^p")

            try:
                return self._wait_for_window(
                    class_name="TNewPrintDlg",
                    timeout=5,
                )
            except EtilabelError:
                if attempt == attempts:
                    break

                time.sleep(1)

        raise EtilabelError(
            "Nie udało się otworzyć okna "
            "drukowania ETILABEL po "
            f"{attempts} próbach."
        )

    def _visible_windows(
        self,
        class_name: str,
    ) -> list[int]:
        return findwindows.find_windows(
            class_name=class_name,
            visible_only=True,
            enabled_only=True,
        )

    def _wait_for_window(
        self,
        class_name: str,
        timeout: int,
    ):
        deadline = time.time() + timeout

        while time.time() < deadline:
            handles = self._visible_windows(
                class_name
            )

            if handles:
                return Desktop(
                    backend="win32"
                ).window(
                    handle=handles[-1]
                )

            time.sleep(0.25)

        raise EtilabelError(
            f"Nie znaleziono okna {class_name!r} "
            f"w ciągu {timeout} sekund."
        )

    def _wait_for_quantity_fields(
        self,
        print_dialog,
    ):
        deadline = (
            time.time() + self.window_timeout
        )

        while time.time() < deadline:
            edits = print_dialog.descendants(
                class_name="TEdit"
            )

            if len(edits) >= 3:
                edits.sort(
                    key=lambda control: (
                        control.rectangle().top,
                        control.rectangle().left,
                    )
                )

                quantity_fields = edits[:2]

                quantity_fields.sort(
                    key=lambda control: (
                        control.rectangle().left
                    )
                )

                return (
                    quantity_fields[0],
                    quantity_fields[1],
                )

            time.sleep(0.25)

        raise EtilabelError(
            "Okno drukowania zostało otwarte, "
            "ale nie znaleziono pól ilości."
        )

    def _cancel_print_dialog(
        self,
        print_dialog,
    ) -> None:
        cancel_button = print_dialog.child_window(
            title="Anuluj",
            class_name="TButton",
        )

        if cancel_button.exists():
            print_dialog.set_focus()
            time.sleep(0.25)
            cancel_button.click_input()
            time.sleep(0.5)

    def _wait_for_print_completion(
        self,
        print_dialog,
    ) -> None:
        deadline = (
            time.time() + self.print_timeout
        )

        while time.time() < deadline:
            if (
                not print_dialog.exists()
                or not print_dialog.is_visible()
            ):
                return

            time.sleep(0.5)

        raise EtilabelError(
            "ETILABEL nie zamknął okna drukowania. "
            "Sprawdź połączenie z drukarką "
            "i komunikaty wyświetlone przez program."
        )

    def _cleanup(
        self,
        main_window,
        print_dialog,
        launched_process: subprocess.Popen,
    ) -> None:
        try:
            if (
                print_dialog is not None
                and print_dialog.exists()
                and print_dialog.is_visible()
            ):
                self._cancel_print_dialog(
                    print_dialog
                )
        except Exception:
            pass

        main_handle = None

        try:
            if (
                main_window is not None
                and main_window.exists()
            ):
                main_handle = main_window.handle
                main_window.close()
        except Exception:
            pass

        if main_handle is not None:
            self._wait_until_window_closed(
                handle=main_handle,
                timeout=5,
            )

        if launched_process.poll() is None:
            try:
                launched_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                launched_process.terminate()

                try:
                    launched_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    launched_process.kill()

    def _wait_until_window_closed(
        self,
        handle: int,
        timeout: int,
    ) -> None:
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                window = Desktop(
                    backend="win32"
                ).window(
                    handle=handle
                )

                if (
                    not window.exists()
                    or not window.is_visible()
                ):
                    return

            except Exception:
                return

            time.sleep(0.25)