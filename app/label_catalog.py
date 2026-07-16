from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.order_reader import (
    OrderItem,
    ParsedOrder,
)


COUNTRY_FOLDERS = {
    "BUŁGARIA": "Bułgarski BG",
    "CZECHY": "Czeski CS",
    "FINLANDIA": "Fiński FI",
    "FRANCJA": "Francja FR",
    "HOLANDIA": "Holandia NL",
    "LITWA": "Litewski LT",
    "PORTUGALIA": "Portugalski PT",
    "RUMUNIA": "Rumunia RO",
    "SŁOWENIA": "Słoweński SL",
    "WĘGRY": "Węgierski HU",
    "WŁOCHY": "Włoski IT",
}


# Aliasy wskazują istniejące foldery tylko
# wtedy, gdy nie ma dopasowania dokładnego.
# Nie zmieniają nazw produktów ani folderów.
PRODUCT_FOLDER_ALIASES = {
    "Hybrid Glass Cleaner": "Hybrid Glass",
    "PRO Slip": "Slippy",
    "Rinseless Shampoo": "Rinsless shampoo",
    "Tar Remover Thixotropic": (
        "Tar Remover Tixotropic"
    ),
}


class LabelCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class LabelJob:
    item: OrderItem
    product_folder_name: str
    label_format: str
    label_path: Path
    quantity: int


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
        return sum(
            job.quantity
            for job in self.jobs_45x45
        )

    @property
    def total_45x110(self) -> int:
        return sum(
            job.quantity
            for job in self.jobs_45x110
        )


def normalize_name(value: str) -> str:
    text = str(value or "").strip().casefold()

    decomposed = unicodedata.normalize(
        "NFKD",
        text,
    )

    without_accents = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(
            character
        )
    )

    return re.sub(
        r"[^a-z0-9]+",
        "",
        without_accents,
    )


def resolve_country_folder(
    labels_root: str | Path,
    country: str,
) -> Path:
    root = Path(labels_root)

    if not root.is_dir():
        raise LabelCatalogError(
            "Nie znaleziono głównego folderu "
            f"etykiet: {root}"
        )

    folder_name = COUNTRY_FOLDERS.get(
        country
    )

    if folder_name is None:
        supported = ", ".join(
            COUNTRY_FOLDERS.keys()
        )

        raise LabelCatalogError(
            f"Nieobsługiwane państwo: "
            f"{country}. "
            f"Obsługiwane państwa: "
            f"{supported}."
        )

    expected_path = root / folder_name

    if expected_path.is_dir():
        return expected_path

    # Dodatkowe wyszukiwanie odporne na
    # wielkość liter i polskie znaki.
    expected_normalized = normalize_name(
        folder_name
    )

    for candidate in root.iterdir():
        if (
            candidate.is_dir()
            and normalize_name(candidate.name)
            == expected_normalized
        ):
            return candidate

    raise LabelCatalogError(
        "Nie znaleziono folderu państwa: "
        f"{expected_path}"
    )


def index_product_folders(
    country_folder: str | Path,
) -> dict[str, Path]:
    folder = Path(country_folder)

    if not folder.is_dir():
        raise LabelCatalogError(
            "Nie znaleziono folderu państwa: "
            f"{folder}"
        )

    index: dict[str, Path] = {}

    for candidate in folder.iterdir():
        if not candidate.is_dir():
            continue

        normalized = normalize_name(
            candidate.name
        )

        if not normalized:
            continue

        if normalized in index:
            previous = index[normalized]

            raise LabelCatalogError(
                "W folderze państwa znajdują "
                "się dwa foldery produktów "
                "o takiej samej uproszczonej "
                "nazwie: "
                f"'{previous.name}' oraz "
                f"'{candidate.name}'."
            )

        index[normalized] = candidate

    return index


def _resolve_product_folder(
    product_folders: dict[str, Path],
    product_folder_name: str,
) -> Path | None:
    normalized_name = normalize_name(
        product_folder_name
    )

    # Dopasowanie dokładne ma zawsze
    # pierwszeństwo przed aliasem.
    direct_match = product_folders.get(
        normalized_name
    )

    if direct_match is not None:
        return direct_match

    normalized_aliases = {
        normalize_name(source_name): (
            normalize_name(target_folder)
        )
        for source_name, target_folder
        in PRODUCT_FOLDER_ALIASES.items()
    }

    alias_target = normalized_aliases.get(
        normalized_name
    )

    if alias_target is None:
        return None

    return product_folders.get(
        alias_target
    )


def _find_label_file(
    product_folder: Path,
    label_format: str,
) -> Path | None:
    expected_filename = (
        f"{label_format}.etx"
    )

    expected_path = (
        product_folder
        / expected_filename
    )

    if expected_path.is_file():
        return expected_path

    expected_normalized = (
        expected_filename.casefold()
    )

    for candidate in product_folder.iterdir():
        if (
            candidate.is_file()
            and candidate.name.casefold()
            == expected_normalized
        ):
            return candidate

    return None


def _build_aggregated_jobs(
    collected_jobs: dict[
        tuple[str, str],
        dict,
    ],
) -> list[LabelJob]:
    jobs: list[LabelJob] = []

    for job_data in collected_jobs.values():
        jobs.append(
            LabelJob(
                item=job_data["item"],
                product_folder_name=(
                    job_data[
                        "product_folder_name"
                    ]
                ),
                label_format=(
                    job_data["label_format"]
                ),
                label_path=(
                    job_data["label_path"]
                ),
                quantity=(
                    job_data["quantity"]
                ),
            )
        )

    return sorted(
        jobs,
        key=lambda job: (
            job.product_folder_name.casefold()
        ),
    )


def build_print_plan(
    order: ParsedOrder,
    labels_root: str | Path,
) -> PrintPlan:
    country_folder = (
        resolve_country_folder(
            labels_root,
            order.country,
        )
    )

    product_folders = (
        index_product_folders(
            country_folder
        )
    )

    collected_45x45: dict[
        tuple[str, str],
        dict,
    ] = {}

    collected_45x110: dict[
        tuple[str, str],
        dict,
    ] = {}

    skipped_items: list[
        SkippedItem
    ] = []

    invalid_items: list[
        InvalidItem
    ] = []

    for item in order.items:
        product_folder = (
            _resolve_product_folder(
                product_folders,
                item.product_folder_name,
            )
        )

        # Brak folderu oznacza, że produkt
        # nie wymaga przeklejki. Dotyczy to
        # np. akcesoriów i maszyn.
        if product_folder is None:
            skipped_items.append(
                SkippedItem(
                    item=item,
                    reason=(
                        "Brak folderu produktu "
                        "w katalogu etykiet – "
                        "przeklejka nie jest "
                        "wymagana."
                    ),
                )
            )
            continue

        # Korzystamy z formatu wyliczonego
        # wcześniej przez order_reader.
        #
        # Dzięki temu działają również
        # wyjątki SPIRITS, KIT i SET.
        label_format = item.label_format

        if label_format not in {
            "45x45",
            "45x110",
        }:
            invalid_items.append(
                InvalidItem(
                    item=item,
                    error=(
                        "Produkt ma folder "
                        "etykiet, ale nie udało "
                        "się określić formatu. "
                        "Rozpoznawane są "
                        "pojemności 0,2L, 0,5L, "
                        "1L, 5L i 10L, produkty "
                        "KIT i SET oraz SPIRITS."
                    ),
                )
            )
            continue

        label_path = _find_label_file(
            product_folder,
            label_format,
        )

        if label_path is None:
            invalid_items.append(
                InvalidItem(
                    item=item,
                    error=(
                        f"W folderze "
                        f"'{product_folder.name}' "
                        f"brakuje pliku "
                        f"'{label_format}.etx'."
                    ),
                )
            )
            continue

        aggregation_key = (
            normalize_name(
                product_folder.name
            ),
            label_format,
        )

        if label_format == "45x45":
            destination = collected_45x45
        else:
            destination = collected_45x110

        existing_job = destination.get(
            aggregation_key
        )

        if existing_job is None:
            destination[
                aggregation_key
            ] = {
                "item": item,
                "product_folder_name": (
                    product_folder.name
                ),
                "label_format": (
                    label_format
                ),
                "label_path": label_path,
                "quantity": item.quantity,
            }
        else:
            existing_job["quantity"] += (
                item.quantity
            )

    jobs_45x45 = (
        _build_aggregated_jobs(
            collected_45x45
        )
    )

    jobs_45x110 = (
        _build_aggregated_jobs(
            collected_45x110
        )
    )

    return PrintPlan(
        country=order.country,
        country_folder=country_folder,
        jobs_45x45=jobs_45x45,
        jobs_45x110=jobs_45x110,
        skipped_items=skipped_items,
        invalid_items=invalid_items,
    )