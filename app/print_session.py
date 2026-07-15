from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from app.audit_log import audit_event


class PrintSessionError(RuntimeError):
    pass


def _friendly_error(error: Exception) -> str:
    technical_error = str(error)
    normalized = technical_error.casefold()

    if "not a valid window handle" in normalized:
        return (
            "Okno programu ETILABEL zostało "
            "zamknięte lub przestało odpowiadać."
        )

    if "tnewprintdlg" in normalized:
        return (
            "Nie udało się otworzyć okna "
            "drukowania w programie ETILABEL."
        )

    if "tnewmainform" in normalized:
        return (
            "Nie udało się otworzyć głównego "
            "okna programu ETILABEL."
        )

    if "timeout" in normalized:
        return (
            "Program ETILABEL nie odpowiedział "
            "w wymaganym czasie."
        )

    if (
        "printer" in normalized
        and "offline" in normalized
    ):
        return (
            "Drukarka jest niedostępna "
            "lub offline."
        )

    return technical_error


class SessionStatus(str, Enum):
    READY = "ready"
    PRINTING_45X45 = "printing_45x45"
    WAITING_FOR_110 = "waiting_for_110"
    PRINTING_45X110 = "printing_45x110"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass(frozen=True)
class CompletedJob:
    stage: str
    product_folder_name: str
    label_path: str
    quantity: int
    result: str


@dataclass(frozen=True)
class FailedJob:
    stage: str
    product_folder_name: str
    label_path: str
    quantity: int
    error: str


@dataclass(frozen=True)
class SeparatorJob:
    product_folder_name: str
    label_path: Path
    quantity: int = 1


class PrintSession:
    def __init__(
        self,
        plan,
        controller,
        test_mode: bool = True,
    ) -> None:
        self.plan = plan
        self.controller = controller
        self.test_mode = bool(test_mode)

        self._lock = threading.RLock()

        self._status = SessionStatus.READY

        self._completed_jobs: list[
            CompletedJob
        ] = []

        self._next_45x45_index = 0
        self._next_45x110_index = 0

        self._failed_job_object = None
        self._failed_stage: str | None = None
        self._failed_job: FailedJob | None = None
        self._last_error: str | None = None

        # Informacja, czy separator przed
        # aktualnym produktem został już
        # poprawnie wydrukowany.
        self._separator_completed_for: (
            tuple[str, int] | None
        ) = None

        self._completed_separators_45x45 = 0
        self._completed_separators_45x110 = 0

        labels_root = Path(
            plan.country_folder
        ).parent

        system_folder = (
            labels_root / "_SYSTEM"
        )

        self._separator_paths = {
            "45x45": (
                system_folder / "45x45.etx"
            ),
            "45x110": (
                system_folder / "45x110.etx"
            ),
        }

        self._validate_separator_templates()

        audit_event(
            "print_session_created",
            country=getattr(
                plan,
                "country",
                "",
            ),
            jobs_45x45=len(
                plan.jobs_45x45
            ),
            jobs_45x110=len(
                plan.jobs_45x110
            ),
            separators_45x45=max(
                0,
                len(plan.jobs_45x45) - 1,
            ),
            separators_45x110=max(
                0,
                len(plan.jobs_45x110) - 1,
            ),
            total_labels=(
                plan.total_45x45
                + plan.total_45x110
            ),
            test_mode=self.test_mode,
        )

    @property
    def status(self) -> SessionStatus:
        with self._lock:
            return self._status

    def _validate_separator_templates(
        self,
    ) -> None:
        stages = (
            (
                "45x45",
                self.plan.jobs_45x45,
            ),
            (
                "45x110",
                self.plan.jobs_45x110,
            ),
        )

        missing_paths: list[Path] = []

        for stage, jobs in stages:
            # Separator jest potrzebny tylko,
            # jeśli dany format zawiera więcej
            # niż jeden produkt.
            if len(jobs) <= 1:
                continue

            separator_path = (
                self._separator_paths[stage]
            )

            if not separator_path.is_file():
                missing_paths.append(
                    separator_path
                )

        if missing_paths:
            missing_text = ", ".join(
                str(path)
                for path in missing_paths
            )

            raise PrintSessionError(
                "Brakuje pustych szablonów "
                "oddzielających produkty: "
                f"{missing_text}"
            )

    def print_45x45(self) -> None:
        with self._lock:
            if self._status != SessionStatus.READY:
                raise PrintSessionError(
                    "Etap 45x45 można rozpocząć "
                    "tylko dla nowej, gotowej "
                    "sesji."
                )

            self._status = (
                SessionStatus.PRINTING_45X45
            )

            self._separator_completed_for = None
            self._clear_failure()

        audit_event(
            "print_stage_started",
            stage="45x45",
            test_mode=self.test_mode,
        )

        self._execute_stage("45x45")

    def print_45x110(self) -> None:
        with self._lock:
            if (
                self._status
                != SessionStatus.WAITING_FOR_110
            ):
                raise PrintSessionError(
                    "Etap 45x110 można rozpocząć "
                    "dopiero po zakończeniu etapu "
                    "45x45 i zmianie rolki."
                )

            self._status = (
                SessionStatus.PRINTING_45X110
            )

            self._separator_completed_for = None
            self._clear_failure()

        audit_event(
            "print_stage_started",
            stage="45x110",
            test_mode=self.test_mode,
        )

        self._execute_stage("45x110")

    def retry_failed_job(self) -> None:
        with self._lock:
            if self._status != SessionStatus.FAILED:
                raise PrintSessionError(
                    "Brak nieudanego zadania "
                    "do ponowienia."
                )

            if (
                self._failed_job_object is None
                or self._failed_stage is None
            ):
                raise PrintSessionError(
                    "Nie udało się odtworzyć "
                    "informacji o błędnej "
                    "etykiecie."
                )

            failed_stage = self._failed_stage

            failed_product = getattr(
                self._failed_job_object,
                "product_folder_name",
                "",
            )

            if failed_stage == "45x45":
                self._status = (
                    SessionStatus.PRINTING_45X45
                )

            elif failed_stage == "45x110":
                self._status = (
                    SessionStatus.PRINTING_45X110
                )

            else:
                raise PrintSessionError(
                    "Nieznany etap błędnego "
                    "zadania."
                )

            self._last_error = None
            self._failed_job = None

        audit_event(
            "failed_label_retry_started",
            stage=failed_stage,
            product=failed_product,
            test_mode=self.test_mode,
        )

        self._execute_stage(
            failed_stage
        )

    def abort(self) -> None:
        with self._lock:
            if self._status not in {
                SessionStatus.FAILED,
                SessionStatus.READY,
                SessionStatus.WAITING_FOR_110,
            }:
                raise PrintSessionError(
                    "Nie można przerwać sesji "
                    "podczas aktywnego sterowania "
                    "programem ETILABEL."
                )

            completed_jobs_count = len(
                self._completed_jobs
            )

            self._status = (
                SessionStatus.ABORTED
            )

            self._failed_job_object = None
            self._failed_stage = None
            self._failed_job = None
            self._last_error = None
            self._separator_completed_for = None

        audit_event(
            "print_session_aborted",
            completed_jobs=(
                completed_jobs_count
            ),
            test_mode=self.test_mode,
        )

    def _execute_stage(
        self,
        stage: str,
    ) -> None:
        jobs = self._jobs_for_stage(
            stage
        )

        while True:
            with self._lock:
                current_index = (
                    self._index_for_stage(
                        stage
                    )
                )

                if current_index >= len(jobs):
                    self._finish_stage(stage)
                    return

                job = jobs[current_index]

            # Separator drukujemy przed każdym
            # produktem poza pierwszym.
            #
            # Jeśli produkt po separatorze ulegnie
            # awarii, informacja o zakończonym
            # separatorze zostaje zachowana.
            # Dzięki temu kliknięcie „Ponów”
            # nie wydrukuje drugiej pustej etykiety.
            if current_index > 0:
                separator_key = (
                    stage,
                    current_index,
                )

                with self._lock:
                    separator_completed = (
                        self._separator_completed_for
                        == separator_key
                    )

                if not separator_completed:
                    self._print_separator(
                        stage=stage,
                        current_index=(
                            current_index
                        ),
                    )

            try:
                result = (
                    self.controller.print_label(
                        label_path=job.label_path,
                        quantity=job.quantity,
                        test_mode=self.test_mode,
                    )
                )

            except Exception as exc:
                self._record_failure(
                    stage=stage,
                    job=job,
                    error=exc,
                )

                raise PrintSessionError(
                    "Nie udało się wydrukować "
                    f"etykiety "
                    f"'{job.product_folder_name}': "
                    f"{_friendly_error(exc)}"
                ) from exc

            self._record_success(
                stage=stage,
                job=job,
                result=result,
            )

    def _print_separator(
        self,
        stage: str,
        current_index: int,
    ) -> None:
        separator_path = (
            self._separator_paths[stage]
        )

        separator_job = SeparatorJob(
            product_folder_name=(
                "Pusta etykieta oddzielająca"
            ),
            label_path=separator_path,
            quantity=1,
        )

        audit_event(
            "separator_started",
            stage=stage,
            before_product_index=(
                current_index
            ),
            label_path=str(
                separator_path
            ),
            test_mode=self.test_mode,
        )

        try:
            self.controller.print_label(
                label_path=separator_path,
                quantity=1,
                test_mode=self.test_mode,
            )

        except Exception as exc:
            self._record_failure(
                stage=stage,
                job=separator_job,
                error=exc,
            )

            raise PrintSessionError(
                "Nie udało się wydrukować "
                "pustej etykiety oddzielającej "
                f"produkty: {_friendly_error(exc)}"
            ) from exc

        with self._lock:
            self._separator_completed_for = (
                stage,
                current_index,
            )

            if stage == "45x45":
                self._completed_separators_45x45 += 1

            elif stage == "45x110":
                self._completed_separators_45x110 += 1

        audit_event(
            "separator_completed",
            stage=stage,
            before_product_index=(
                current_index
            ),
            label_path=str(
                separator_path
            ),
            test_mode=self.test_mode,
        )

    def _jobs_for_stage(
        self,
        stage: str,
    ):
        if stage == "45x45":
            return self.plan.jobs_45x45

        if stage == "45x110":
            return self.plan.jobs_45x110

        raise PrintSessionError(
            f"Nieznany etap druku: {stage}"
        )

    def _index_for_stage(
        self,
        stage: str,
    ) -> int:
        if stage == "45x45":
            return self._next_45x45_index

        if stage == "45x110":
            return self._next_45x110_index

        raise PrintSessionError(
            f"Nieznany etap druku: {stage}"
        )

    def _increase_stage_index(
        self,
        stage: str,
    ) -> None:
        if stage == "45x45":
            self._next_45x45_index += 1
            return

        if stage == "45x110":
            self._next_45x110_index += 1
            return

        raise PrintSessionError(
            f"Nieznany etap druku: {stage}"
        )

    def _record_success(
        self,
        stage: str,
        job,
        result: Any,
    ) -> None:
        with self._lock:
            self._completed_jobs.append(
                CompletedJob(
                    stage=stage,
                    product_folder_name=(
                        job.product_folder_name
                    ),
                    label_path=str(
                        job.label_path
                    ),
                    quantity=int(
                        job.quantity
                    ),
                    result=str(result),
                )
            )

            self._increase_stage_index(
                stage
            )

            # Produkt po separatorze został
            # zakończony. Dla następnego
            # produktu potrzebny będzie nowy
            # separator.
            self._separator_completed_for = None

            self._clear_failure()

        audit_event(
            "label_completed",
            stage=stage,
            product=(
                job.product_folder_name
            ),
            quantity=int(
                job.quantity
            ),
            label_path=str(
                job.label_path
            ),
            test_mode=self.test_mode,
        )

    def _record_failure(
        self,
        stage: str,
        job,
        error: Exception,
    ) -> None:
        error_text = _friendly_error(
            error
        )

        with self._lock:
            self._failed_job_object = job
            self._failed_stage = stage
            self._last_error = error_text

            self._failed_job = FailedJob(
                stage=stage,
                product_folder_name=(
                    job.product_folder_name
                ),
                label_path=str(
                    job.label_path
                ),
                quantity=int(
                    job.quantity
                ),
                error=error_text,
            )

            self._status = (
                SessionStatus.FAILED
            )

        audit_event(
            "label_failed",
            level="error",
            stage=stage,
            product=(
                job.product_folder_name
            ),
            quantity=int(
                job.quantity
            ),
            label_path=str(
                job.label_path
            ),
            error=error_text,
            test_mode=self.test_mode,
        )

    def _clear_failure(self) -> None:
        self._failed_job_object = None
        self._failed_stage = None
        self._failed_job = None
        self._last_error = None

    def _finish_stage(
        self,
        stage: str,
    ) -> None:
        with self._lock:
            self._clear_failure()
            self._separator_completed_for = None

            if stage == "45x45":
                self._status = (
                    SessionStatus.WAITING_FOR_110
                )

            elif stage == "45x110":
                self._status = (
                    SessionStatus.COMPLETED
                )

            else:
                raise PrintSessionError(
                    "Nieznany etap druku: "
                    f"{stage}"
                )

        audit_event(
            "print_stage_completed",
            stage=stage,
            completed_jobs=len(
                self._completed_jobs
            ),
            completed_separators=(
                self._completed_separators_for_stage(
                    stage
                )
            ),
            test_mode=self.test_mode,
        )

    def _completed_separators_for_stage(
        self,
        stage: str,
    ) -> int:
        if stage == "45x45":
            return (
                self._completed_separators_45x45
            )

        if stage == "45x110":
            return (
                self._completed_separators_45x110
            )

        return 0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total_45x45 = len(
                self.plan.jobs_45x45
            )

            total_45x110 = len(
                self.plan.jobs_45x110
            )

            completed_45x45 = (
                self._next_45x45_index
            )

            completed_45x110 = (
                self._next_45x110_index
            )

            total_separators_45x45 = max(
                0,
                total_45x45 - 1,
            )

            total_separators_45x110 = max(
                0,
                total_45x110 - 1,
            )

            return {
                "status": self._status.value,
                "test_mode": self.test_mode,
                "error": self._last_error,
                "failed_job": self._failed_job,
                "completed_jobs": list(
                    self._completed_jobs
                ),
                "progress": {
                    "completed_45x45": (
                        completed_45x45
                    ),
                    "total_45x45": (
                        total_45x45
                    ),
                    "completed_45x110": (
                        completed_45x110
                    ),
                    "total_45x110": (
                        total_45x110
                    ),
                    "completed_total": (
                        completed_45x45
                        + completed_45x110
                    ),
                    "total_jobs": (
                        total_45x45
                        + total_45x110
                    ),
                    "completed_separators_45x45": (
                        self
                        ._completed_separators_45x45
                    ),
                    "total_separators_45x45": (
                        total_separators_45x45
                    ),
                    "completed_separators_45x110": (
                        self
                        ._completed_separators_45x110
                    ),
                    "total_separators_45x110": (
                        total_separators_45x110
                    ),
                },
            }