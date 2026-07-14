from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook


CAPACITY_PATTERN = re.compile(
    r"(\d+(?:[,.]\d+)?)\s*l\s*$",
    flags=re.IGNORECASE,
)

COUNTRY_PATTERN = re.compile(
    r"^zam[oó]wienie[\s_-]+(.+)$",
    flags=re.IGNORECASE,
)

LABEL_FORMATS = {
    Decimal("0.2"): "45x45",
    Decimal("0.5"): "45x45",
    Decimal("1"): "45x110",
    Decimal("5"): "45x110",
    Decimal("10"): "45x110",
}


class OrderReadError(Exception):
    """Błąd uniemożliwiający odczyt zamówienia."""


@dataclass(frozen=True)
class OrderItem:
    row_number: int
    product_code: str
    catalog_number: str
    product_name: str
    quantity: int
    unit: str
    capacity_liters: Decimal | None
    label_format: str | None
    product_folder_name: str


@dataclass(frozen=True)
class ParsedOrder:
    source_path: Path
    country: str
    items: list[OrderItem]


def normalize_header(value: object) -> str:
    text = unicodedata.normalize(
        "NFKD",
        str(value or ""),
    )
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )

    return re.sub(
        r"[^a-z0-9]",
        "",
        text.casefold(),
    )


def extract_country(file_path: str | Path) -> str:
    path = Path(file_path)
    filename_without_extension = path.stem

    match = re.search(
        r"zam[oó]wienie[\s_-]+(.+)$",
        filename_without_extension,
        flags=re.IGNORECASE,
    )

    if not match:
        raise OrderReadError(
            "Nazwa pliku powinna zawierać "
            "'ZAMÓWIENIE PAŃSTWO.xlsx', np. "
            "'ZAMÓWIENIE RUMUNIA.xlsx'."
        )

    country = match.group(1)
    country = re.sub(r"[_-]+", " ", country)
    country = re.sub(r"\s+", " ", country).strip()

    if not country:
        raise OrderReadError(
            "Nie udało się odczytać państwa "
            "z nazwy pliku."
        )

    return country.upper()


def extract_capacity_liters(
    product_name: str,
) -> Decimal | None:
    match = CAPACITY_PATTERN.search(product_name)

    if not match:
        return None

    value = match.group(1).replace(",", ".")

    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise OrderReadError(
            "Nieprawidłowa pojemność w nazwie: "
            f"{product_name!r}."
        ) from error


def select_label_format(
    capacity_liters: Decimal | None,
) -> str | None:
    if capacity_liters is None:
        return None

    return LABEL_FORMATS.get(capacity_liters)


def extract_product_folder_name(
    product_name: str,
) -> str:
    name = re.sub(
        r"^\s*ADBL\s+",
        "",
        product_name,
        flags=re.IGNORECASE,
    )

    name = CAPACITY_PATTERN.sub("", name)

    return name.strip()


def parse_quantity(
    value: object,
    row_number: int,
) -> int:
    try:
        quantity = Decimal(
            str(value).strip().replace(",", ".")
        )
    except InvalidOperation as error:
        raise OrderReadError(
            f"Nieprawidłowa ilość w wierszu "
            f"{row_number}: {value!r}."
        ) from error

    if quantity <= 0:
        raise OrderReadError(
            f"Ilość w wierszu {row_number} "
            "musi być większa od zera."
        )

    if quantity != quantity.to_integral_value():
        raise OrderReadError(
            f"Ilość etykiet w wierszu {row_number} "
            "musi być liczbą całkowitą."
        )

    return int(quantity)


def read_order(
    file_path: str | Path,
) -> ParsedOrder:
    path = Path(file_path)

    if not path.exists():
        raise OrderReadError(
            f"Nie znaleziono pliku: {path}"
        )

    if path.suffix.casefold() != ".xlsx":
        raise OrderReadError(
            "Obsługiwane są wyłącznie pliki XLSX."
        )

    country = extract_country(path)

    workbook = load_workbook(
        path,
        read_only=True,
        data_only=True,
    )

    try:
        worksheet = workbook.active

        header_row = next(
            worksheet.iter_rows(
                min_row=1,
                max_row=1,
                values_only=True,
            ),
            None,
        )

        if header_row is None:
            raise OrderReadError(
                "Arkusz jest pusty."
            )

        headers = {
            normalize_header(value): column_number
            for column_number, value in enumerate(
                header_row,
                start=1,
            )
            if value is not None
        }

        required_headers = {
            "product_code": "towarkod",
            "catalog_number": (
                "towarnumerkatalogowy"
            ),
            "product_name": "towarnazwa",
            "quantity": "ilosc",
        }

        missing_headers = [
            header
            for header in required_headers.values()
            if header not in headers
        ]

        if missing_headers:
            raise OrderReadError(
                "W arkuszu brakuje wymaganych kolumn: "
                + ", ".join(missing_headers)
            )

        unit_column = headers.get(
            "iloscjednostka"
        )

        items: list[OrderItem] = []

        for row_number, row in enumerate(
            worksheet.iter_rows(
                min_row=2,
                values_only=True,
            ),
            start=2,
        ):
            def value_at(
                column_number: int,
            ) -> object:
                index = column_number - 1

                if index >= len(row):
                    return None

                return row[index]

            product_name_value = value_at(
                headers["towarnazwa"]
            )

            if product_name_value is None:
                continue

            product_name = str(
                product_name_value
            ).strip()

            if not product_name:
                continue

            quantity_value = value_at(
                headers["ilosc"]
            )

            capacity = extract_capacity_liters(
                product_name
            )

            unit = ""

            if unit_column is not None:
                unit_value = value_at(
                    unit_column
                )
                unit = str(
                    unit_value or ""
                ).strip()

            item = OrderItem(
                row_number=row_number,
                product_code=str(
                    value_at(
                        headers["towarkod"]
                    )
                    or ""
                ).strip(),
                catalog_number=str(
                    value_at(
                        headers[
                            "towarnumerkatalogowy"
                        ]
                    )
                    or ""
                ).strip(),
                product_name=product_name,
                quantity=parse_quantity(
                    quantity_value,
                    row_number,
                ),
                unit=unit,
                capacity_liters=capacity,
                label_format=select_label_format(
                    capacity
                ),
                product_folder_name=(
                    extract_product_folder_name(
                        product_name
                    )
                ),
            )

            items.append(item)

        if not items:
            raise OrderReadError(
                "Zamówienie nie zawiera "
                "żadnych produktów."
            )

        return ParsedOrder(
            source_path=path,
            country=country,
            items=items,
        )

    finally:
        workbook.close()