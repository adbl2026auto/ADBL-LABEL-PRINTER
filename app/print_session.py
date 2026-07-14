from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum

from app.etilabel_controller import (
    EtilabelController,
    PrintResult,
)
from app.label_catalog import LabelJob, PrintPlan


class PrintSessionError(Exception):
    """Błąd stanu sesji drukowania."""


class SessionStatus(StrEnum):
    READY = "ready"
    PRINTING_45X45 = "printing_45x45"
    WAITING_FOR_110 = "waiting_for_110"
    PRINTING_45X110 = "printing_45x110"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class CompletedJob:
    product_name: str
    product_folder_name: str
    label_format: str
    quantity: int
    label_path: str
    printed: bool
    message: str


class PrintSession:
    def __init__(
        self,
        plan: PrintPlan,
        controller: EtilabelController,
        test_mode: bool = True,
    ):
        self.session_id = str(uuid.uuid4())
        self.plan = plan
        self.controller = controller
        self.test_mode = test_mode

        self.status = SessionStatus.READY
        self.current_job: LabelJob | None = None
        self.current_job_number = 0
        self.current_stage_total = 0

        self.completed_jobs: list[CompletedJob] = []
        self.error: str | None = None

        self._lock = threading.RLock()

    def print_45x45(self) -> None:
        with self._lock:
            if self.status != SessionStatus.READY:
                raise PrintSessionError(
                    "Drukowanie 45x45 można rozpocząć "
                    "wyłącznie dla nowej sesji."
                )

            self.status = SessionStatus.PRINTING_45X45
            self.current_job_number = 0
            self.current_stage_total = len(
                self.plan.jobs_45x45
            )
            self.error = None

        try:
            self._execute_jobs(
                self.plan.jobs_45x45
            )

            with self._lock:
                self.current_job = None

                if self.plan.jobs_45x110:
                    self.status = (
                        SessionStatus.WAITING_FOR_110
                    )
                else:
                    self.status = (
                        SessionStatus.COMPLETED
                    )

        except Exception as error:
            self._mark_failed(error)
            raise

    def print_45x110(self) -> None:
        with self._lock:
            if (
                self.status
                != SessionStatus.WAITING_FOR_110
            ):
                raise PrintSessionError(
                    "Drukowanie 45x110 można rozpocząć "
                    "dopiero po zakończeniu etapu 45x45."
                )

            self.status = (
                SessionStatus.PRINTING_45X110
            )
            self.current_job_number = 0
            self.current_stage_total = len(
                self.plan.jobs_45x110
            )
            self.error = None

        try:
            self._execute_jobs(
                self.plan.jobs_45x110
            )

            with self._lock:
                self.current_job = None
                self.status = SessionStatus.COMPLETED

        except Exception as error:
            self._mark_failed(error)
            raise

    def _execute_jobs(
        self,
        jobs: list[LabelJob],
    ) -> None:
        for job_number, job in enumerate(
            jobs,
            start=1,
        ):
            with self._lock:
                self.current_job = job
                self.current_job_number = job_number

            result = self.controller.print_label(
                label_path=job.label_path,
                quantity=job.quantity,
                test_mode=self.test_mode,
            )

            self._record_result(
                job=job,
                result=result,
            )

    def _record_result(
        self,
        job: LabelJob,
        result: PrintResult,
    ) -> None:
        completed_job = CompletedJob(
            product_name=job.product_name,
            product_folder_name=(
                job.product_folder_name
            ),
            label_format=job.label_format,
            quantity=job.quantity,
            label_path=str(job.label_path),
            printed=result.printed,
            message=result.message,
        )

        with self._lock:
            self.completed_jobs.append(
                completed_job
            )

    def _mark_failed(
        self,
        error: Exception,
    ) -> None:
        with self._lock:
            self.status = SessionStatus.FAILED
            self.error = str(error)
            self.current_job = None

    def snapshot(self) -> dict:
        with self._lock:
            current_job = None

            if self.current_job is not None:
                current_job = {
                    "product_name": (
                        self.current_job.product_name
                    ),
                    "product_folder_name": (
                        self.current_job
                        .product_folder_name
                    ),
                    "label_format": (
                        self.current_job.label_format
                    ),
                    "quantity": (
                        self.current_job.quantity
                    ),
                }

            return {
                "session_id": self.session_id,
                "status": self.status.value,
                "country": self.plan.country,
                "test_mode": self.test_mode,
                "current_job": current_job,
                "current_job_number": (
                    self.current_job_number
                ),
                "current_stage_total": (
                    self.current_stage_total
                ),
                "totals": {
                    "jobs_45x45": len(
                        self.plan.jobs_45x45
                    ),
                    "labels_45x45": (
                        self.plan.total_45x45
                    ),
                    "jobs_45x110": len(
                        self.plan.jobs_45x110
                    ),
                    "labels_45x110": (
                        self.plan.total_45x110
                    ),
                    "labels_all": (
                        self.plan.total_labels
                    ),
                },
                "completed_jobs": [
                    asdict(job)
                    for job in self.completed_jobs
                ],
                "error": self.error,
            }