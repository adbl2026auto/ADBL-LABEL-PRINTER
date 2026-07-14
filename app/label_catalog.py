from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.order_reader import OrderItem, ParsedOrder


POLISH_TRANSLATION = str.maketrans({
    "ł": "l",
    "Ł": "L",
})

COUNTRY_FOLDERS = {
    "bulgaria": "Bułgarski BG",
    "bg": "Bułgarski BG",
    "czechy": "Czeski CS",
    "cs": "Czeski CS",
    "finlandia": "Fiński FI",
    "fi": "Fiński FI",
    "litwa": "Litewski LT",
    "lt": "Litewski LT",
    "portugalia": "Portugalski PT",
    "pt": "Portugalski PT",
    "rumunia": "Rumunia RO",
    "ro": "Rumunia RO",
    "slowenia": "Słoweński SL",
    "sl": "Słoweński SL",
    "wegry": "Węgierski HU",
    "hu": "Węgierski HU",
    "wlochy": "Włoski IT",
    "it": "Włoski IT",
}


class LabelCatalogError(Exception):
    """Błąd konfiguracji lub struktury katalogu etykiet."""


@dataclass(frozen=True)
class LabelJob:
    label_path: Path
    product_name: str
    product_folder_name: str
    label_format: str
    quantity: int
    row_numbers: tuple[int, ...]


@dataclass(frozen=True)
class SkippedItem:
    item: OrderItem
    reason: str


@dataclass(frozen=True)
class InvalidItem:
    item: OrderItem
    error: str


@dataclass(frozen=True)
class PrintPlan:
    country: str
    country_folder: Path
    jobs_45x45: list[LabelJob]
    jobs_45x110: list[LabelJob]
    skipped_items: list[SkippedItem]
    invalid_items: list[InvalidItem]

    @property
    def total_45x45(self) -> int:
        return sum(job.quantity for job in self.jobs_45x45)

    @property
    def total_45x110(self) -> int:
        return sum(job.quantity for job in self.jobs_45x110)

    @property
    def total_labels(self) -> int:
        return self.total_45x45 + self.total_45x110


def normalize_name(value: str) -> str:
    text = value.translate(POLISH_TRANSLATION)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def resolve_country_folder(
    labels_root: str | Path,
    country: str,
) -> Path:
    root = Path(labels_root)

    if not root.exists():
        raise LabelCatalogError(
            f"Nie znaleziono katalogu etykiet: {root}"
        )

    if not root.is_dir():
        raise LabelCatalogError(
            f"Ścieżka etykiet nie jest folderem: {root}"
        )

    normalized_country = normalize_name(country)
    configured_name = COUNTRY_FOLDERS.get(normalized_country)

    if configured_name is not None:
        configured_path = root / configured_name

        if configured_path.is_dir():
            return configured_path

        raise LabelCatalogError(
            f"Nie znaleziono folderu państwa: {configured_path}"
        )

    # Dodatkowa próba odnalezienia folderu po fragmencie nazwy.
    candidates = [
        path
        for path in root.iterdir()
        if (
            path.is_dir()
            and normalized_country in normalize_name(path.name)
        )
    ]

    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1:
        raise LabelCatalogError(
            f"Znaleziono kilka folderów dla państwa {country!r}: "
            + ", ".join(path.name for path in candidates)
        )

    raise LabelCatalogError(
        f"Nie skonfigurowano folderu dla państwa: {country}"
    )


def index_product_folders(
    country_folder: Path,
) -> dict[str, Path]:
    index: dict[str, Path] = {}

    for path in country_folder.iterdir():
        if not path.is_dir():
            continue

        # Foldery techniczne nie uczestniczą w dopasowaniu.
        if path.name.startswith("_"):
            continue

        key = normalize_name(path.name)

        if not key:
            continue

        if key in index:
            raise LabelCatalogError(
                "Niejednoznaczne foldery produktów: "
                f"{index[key].name!r} oraz {path.name!r}."
            )

        index[key] = path

    return index


def build_print_plan(
    order: ParsedOrder,
    labels_root: str | Path,
) -> PrintPlan:
    country_folder = resolve_country_folder(
        labels_root,
        order.country,
    )
    product_folders = index_product_folders(country_folder)

    skipped_items: list[SkippedItem] = []
    invalid_items: list[InvalidItem] = []

    accumulators: dict[Path, dict[str, object]] = {}

    for item in order.items:
        product_key = normalize_name(
            item.product_folder_name
        )
        product_folder = product_folders.get(product_key)

        if product_folder is None:
            skipped_items.append(
                SkippedItem(
                    item=item,
                    reason=(
                        "Brak folderu produktu w folderze państwa — "
                        "produkt nie wymaga przeklejki albo jego nazwa "
                        "nie została dopasowana."
                    ),
                )
            )
            continue

        if item.capacity_liters is None:
            invalid_items.append(
                InvalidItem(
                    item=item,
                    error=(
                        "Produkt ma folder etykiet, ale jego nazwa "
                        "nie zawiera rozpoznanej pojemności."
                    ),
                )
            )
            continue

        if item.label_format is None:
            invalid_items.append(
                InvalidItem(
                    item=item,
                    error=(
                        "Nieobsługiwana pojemność: "
                        f"{item.capacity_liters} L."
                    ),
                )
            )
            continue

        label_path = (
            product_folder
            / f"{item.label_format}.etx"
        )

        if not label_path.is_file():
            invalid_items.append(
                InvalidItem(
                    item=item,
                    error=(
                        "Brak wymaganego pliku etykiety: "
                        f"{label_path.name}"
                    ),
                )
            )
            continue

        accumulator = accumulators.setdefault(
            label_path,
            {
                "product_name": item.product_name,
                "product_folder_name": product_folder.name,
                "label_format": item.label_format,
                "quantity": 0,
                "row_numbers": [],
            },
        )

        accumulator["quantity"] = (
            int(accumulator["quantity"]) + item.quantity
        )
        accumulator["row_numbers"].append(item.row_number)

    jobs = [
        LabelJob(
            label_path=label_path,
            product_name=str(data["product_name"]),
            product_folder_name=str(
                data["product_folder_name"]
            ),
            label_format=str(data["label_format"]),
            quantity=int(data["quantity"]),
            row_numbers=tuple(data["row_numbers"]),
        )
        for label_path, data in accumulators.items()
    ]

    jobs.sort(
        key=lambda job: normalize_name(
            job.product_folder_name
        )
    )

    return PrintPlan(
        country=order.country,
        country_folder=country_folder,
        jobs_45x45=[
            job
            for job in jobs
            if job.label_format == "45x45"
        ],
        jobs_45x110=[
            job
            for job in jobs
            if job.label_format == "45x110"
        ],
        skipped_items=skipped_items,
        invalid_items=invalid_items,
    )