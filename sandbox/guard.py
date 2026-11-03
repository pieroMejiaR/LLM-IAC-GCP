"""Revisión estática previa a terraform init (versión mínima).

Rechaza lo que podría ejecutar código local, sacar datos del sandbox o usar
otro proveedor. Santiago: endurecer esta revisión y cubrir S2 a S4.
"""
from __future__ import annotations

import re

from sandbox.contract import Diagnostico

PROVEEDORES_PERMITIDOS = {"google", "google-beta"}
FUENTES_PERMITIDAS = {"hashicorp/google", "hashicorp/google-beta"}

_RE_PROVIDER = re.compile(r'^\s*provider\s+"([^"]+)"', re.M)
_RE_SOURCE = re.compile(r'source\s*=\s*"([^"]+)"')
_RE_RESOURCE = re.compile(r'^\s*(resource|data)\s+"([^"]+)"\s+"([^"]+)"', re.M)
_RE_MODULE = re.compile(r'^\s*module\s+"([^"]+)"\s*\{(.*?)^\}', re.M | re.S)
_RE_PROHIBIDOS = [
    (re.compile(r'^\s*data\s+"external"', re.M), 'data "external" ejecuta programas locales'),
    (re.compile(r'^\s*data\s+"http"', re.M), 'data "http" hace peticiones de red'),
    (re.compile(r'\bprovisioner\s+"', re.M), "los provisioners ejecutan comandos"),
    (re.compile(r'^\s*backend\s+"', re.M), "no se permite declarar backend"),
    (re.compile(r'^\s*cloud\s*\{', re.M), "no se permite el bloque cloud"),
]


def _linea(texto: str, pos: int) -> int:
    return texto.count("\n", 0, pos) + 1


def revisar(archivos: dict[str, str]) -> list[Diagnostico]:
    diags: list[Diagnostico] = []

    def agregar(mensaje: str, archivo: str, linea: int | None = None) -> None:
        diags.append(Diagnostico(control="guard", mensaje=mensaje, archivo=archivo, linea=linea))

    for nombre, texto in archivos.items():
        for patron, motivo in _RE_PROHIBIDOS:
            for m in patron.finditer(texto):
                agregar(f"Bloque no permitido: {motivo}.", nombre, _linea(texto, m.start()))

        for m in _RE_PROVIDER.finditer(texto):
            if m.group(1) not in PROVEEDORES_PERMITIDOS:
                agregar(f'Proveedor no permitido: "{m.group(1)}". Usa solo google y google-beta.',
                        nombre, _linea(texto, m.start()))

        bloque_rp = re.search(r"required_providers\s*\{(.*?)\n\s*\}", texto, re.S)
        if bloque_rp:
            for m in _RE_SOURCE.finditer(bloque_rp.group(1)):
                if m.group(1).lower() not in FUENTES_PERMITIDAS:
                    agregar(f'Fuente de proveedor no permitida: "{m.group(1)}".', nombre)

        for m in _RE_RESOURCE.finditer(texto):
            tipo = m.group(2)
            prefijo = tipo.split("_", 1)[0]
            if prefijo != "google":
                agregar(f'Tipo "{tipo}" pertenece a un proveedor no permitido.', nombre,
                        _linea(texto, m.start()))

        for m in _RE_MODULE.finditer(texto):
            fuente = _RE_SOURCE.search(m.group(2))
            if not fuente or not fuente.group(1).startswith("./"):
                agregar(f'Módulo "{m.group(1)}" con fuente remota o ausente; solo se permiten módulos locales.',
                        nombre, _linea(texto, m.start()))
    return diags
