from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class PrintSessionError(RuntimeError):
    pass

def _friendly_error(error: Exception) -> str:
    technical_error = str(error)
    normalized = technical_error.casefold()

    if "not a valid window handle" in normalized:
        return (
            "Okno programu ETILABEL zostało zamknięte "
            "lub przestało odpowiadać."
        )

    if "tnewprintdlg" in normalized:
        return (
            "Nie udało się otworzyć okna drukowania "
            "w programie ETILABEL."
        )

    if "tnewmainform" in normalized:
        return (
            "Nie udało się otworzyć głównego okna "
            "programu ETILABEL."
        )

    if "timeout" in normalized:
        return (
            "Program ETILABEL nie odpowiedział "
            "w wymaganym czasie."
        )

    if "printer" in normalized and "offline" in normalized:
        return "Drukarka jest niedostępna lub offline."

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
        self._completed_jobs: list[CompletedJob] = []

        self._next_45x45_index = 0
        self._next_45x110_index = 0

        self._failed_job_object = None
        self._failed_stage: str | None = None
        self._failed_job: FailedJob | None = None
        self._last_error: str | None = None

    @property
    def status(self) -> SessionStatus:
        with self._lock:
            return self._status

    def print_45x45(self) -> None:
        with self._lock:
            if self._status != SessionStatus.READY:
                raise PrintSessionError(
                    "Etap 45x45 można rozpocząć tylko "
                    "dla nowej, gotowej sesji."
                )

            self._status = SessionStatus.PRINTING_45X45
            self._clear_failure()

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
            self._clear_failure()

        self._execute_stage("45x110")

    def retry_failed_job(self) -> None:
        with self._lock:
            if self._status != SessionStatus.FAILED:
                raise PrintSessionError(
                    "Brak nieudanego zadania do "
                    "ponowienia."
                )

            if (
                self._failed_job_object is None
                or self._failed_stage is None
            ):
                raise PrintSessionError(
                    "Nie udało się odtworzyć "
                    "informacji o błędnej etykiecie."
                )

            failed_stage = self._failed_stage

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
                    "Nieznany etap błędnego zadania."
                )

            self._last_error = None
            self._failed_job = None

        # Indeks nie został zwiększony po błędzie.
        # Dzięki temu wykonanie zacznie się od
        # dokładnie tej samej etykiety.
        self._execute_stage(failed_stage)

    def abort(self) -> None:
        with self._lock:
            if self._status not in {
                SessionStatus.FAILED,
                SessionStatus.READY,
                SessionStatus.WAITING_FOR_110,
            }:
                raise PrintSessionError(
                    "Nie można przerwać sesji podczas "
                    "aktywnego sterowania programem "
                    "ETILABEL."
                )

            self._status = SessionStatus.ABORTED
            self._failed_job_object = None
            self._failed_stage = None
            self._failed_job = None

    def _execute_stage(self, stage: str) -> None:
        jobs = self._jobs_for_stage(stage)

        while True:
            with self._lock:
                current_index = self._index_for_stage(
                    stage
                )

                if current_index >= len(jobs):
                    self._finish_stage(stage)
                    return

                job = jobs[current_index]

            try:
                result = self.controller.print_label(
                    label_path=job.label_path,
                    quantity=job.quantity,
                    test_mode=self.test_mode,
                )
            except Exception as exc:
                self._record_failure(
                    stage=stage,
                    job=job,
                    error=exc,
                )

                raise PrintSessionError(
                    f"Nie udało się wydrukować "
                    f"etykiety "
                    f"'{job.product_folder_name}': "
                    f"{exc}"
                ) from exc

            self._record_success(
                stage=stage,
                job=job,
                result=result,
            )

    def _jobs_for_stage(self, stage: str):
        if stage == "45x45":
            return self.plan.jobs_45x45

        if stage == "45x110":
            return self.plan.jobs_45x110

        raise PrintSessionError(
            f"Nieznany etap druku: {stage}"
        )

    def _index_for_stage(self, stage: str) -> int:
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
                    label_path=str(job.label_path),
                    quantity=int(job.quantity),
                    result=str(result),
                )
            )

            self._increase_stage_index(stage)
            self._clear_failure()

    def _record_failure(
        self,
        stage: str,
        job,
        error: Exception,
    ) -> None:
        error_text = _friendly_error(error)

        with self._lock:
            self._failed_job_object = job
            self._failed_stage = stage
            self._last_error = error_text

            self._failed_job = FailedJob(
                stage=stage,
                product_folder_name=(
                    job.product_folder_name
                ),
                label_path=str(job.label_path),
                quantity=int(job.quantity),
                error=error_text,
            )

            self._status = SessionStatus.FAILED

    def _clear_failure(self) -> None:
        self._failed_job_object = None
        self._failed_stage = None
        self._failed_job = None
        self._last_error = None

    def _finish_stage(self, stage: str) -> None:
        with self._lock:
            self._clear_failure()

            if stage == "45x45":
                self._status = (
                    SessionStatus.WAITING_FOR_110
                )
                return

            if stage == "45x110":
                self._status = (
                    SessionStatus.COMPLETED
                )
                return

            raise PrintSessionError(
                f"Nieznany etap druku: {stage}"
            )

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
                    "total_45x45": total_45x45,
                    "completed_45x110": (
                        completed_45x110
                    ),
                    "total_45x110": total_45x110,
                    "completed_total": (
                        completed_45x45
                        + completed_45x110
                    ),
                    "total_jobs": (
                        total_45x45
                        + total_45x110
                    ),
                },
            }