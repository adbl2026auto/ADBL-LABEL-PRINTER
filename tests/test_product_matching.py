from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.label_catalog import (
    _resolve_product_folder,
    index_product_folders,
)
from app.order_reader import (
    extract_capacity_liters,
    extract_country,
    extract_product_folder_name,
    select_label_format,
)


class OrderReaderTests(unittest.TestCase):
    def test_capacity_is_read_and_removed_from_middle(self) -> None:
        name = "ADBL Interior QD 0,5L UNLIMITED"

        self.assertEqual(
            extract_capacity_liters(name),
            0.5,
        )
        self.assertEqual(
            extract_product_folder_name(name),
            "Interior QD UNLIMITED",
        )
        self.assertEqual(
            select_label_format(0.5, name),
            "45x45",
        )

    def test_spirits_always_uses_45x45(self) -> None:
        name = "ADBL SPIRITS HAYS 30ml"

        self.assertEqual(
            extract_product_folder_name(name),
            "SPIRITS HAYS",
        )
        self.assertEqual(
            select_label_format(0.03, name),
            "45x45",
        )
        self.assertEqual(
            select_label_format(
                None,
                "ADBL SPIRITS MISS",
            ),
            "45x45",
        )

    def test_country_without_polish_characters(self) -> None:
        self.assertEqual(
            extract_country(
                "ZAMOWIENIE 19.06 WEGRY.xlsx"
            ),
            "WĘGRY",
        )


class ProductAliasTests(unittest.TestCase):
    def test_aliases_resolve_existing_folders(self) -> None:
        aliases = {
            "Hybrid Glass Cleaner": (
                "Hybrid Glass"
            ),
            "PRO Slip": "Slippy",
            "Rinseless Shampoo": (
                "Rinsless shampoo"
            ),
            "Tar Remover Thixotropic": (
                "Tar Remover Tixotropic"
            ),
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            for folder_name in aliases.values():
                (root / folder_name).mkdir()

            folders = index_product_folders(root)

            for source_name, target_name in (
                aliases.items()
            ):
                resolved = _resolve_product_folder(
                    folders,
                    source_name,
                )

                self.assertIsNotNone(resolved)
                self.assertEqual(
                    resolved.name,
                    target_name,
                )

    def test_exact_match_has_priority_over_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Hybrid Glass").mkdir()
            (
                root / "Hybrid Glass Cleaner"
            ).mkdir()

            folders = index_product_folders(root)
            resolved = _resolve_product_folder(
                folders,
                "Hybrid Glass Cleaner",
            )

            self.assertIsNotNone(resolved)
            self.assertEqual(
                resolved.name,
                "Hybrid Glass Cleaner",
            )

    def test_ceramic_sealant_has_no_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Ceramic QD").mkdir()

            folders = index_product_folders(root)

            self.assertIsNone(
                _resolve_product_folder(
                    folders,
                    "Ceramic Sealant",
                )
            )


if __name__ == "__main__":
    unittest.main()
