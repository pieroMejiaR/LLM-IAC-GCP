"""Plantillas de prompts. Usan string.Template ($variable) para no chocar con las llaves de HCL y JSON."""
from __future__ import annotations

from pathlib import Path
from string import Template

_DIR = Path(__file__).parent


def cargar(nombre: str) -> Template:
    return Template((_DIR / f"{nombre}.md").read_text(encoding="utf-8"))


def render(nombre: str, **valores) -> str:
    return cargar(nombre).substitute(**valores)
