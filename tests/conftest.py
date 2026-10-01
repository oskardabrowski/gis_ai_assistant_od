# -*- coding: utf-8 -*-
"""Testy jednostkowe modułów niezależnych od QGIS: uruchom `pytest` w katalogu nadrzędnym wtyczki."""
import importlib.util
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gis_assistant_ai", root / "__init__.py",
                                            submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules["gis_assistant_ai"] = module
spec.loader.exec_module(module)
