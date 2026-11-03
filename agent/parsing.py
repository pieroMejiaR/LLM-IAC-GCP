"""Lectura de la respuesta del LLM y análisis liviano del código HCL."""
from __future__ import annotations

import json
import re

_RE_ARCHIVO = re.compile(r'<file\s+name\s*=\s*"([^"]+)"\s*>(.*?)</file>', re.S | re.I)
_RE_NOMBRE_VALIDO = re.compile(r"^[a-z0-9][a-z0-9_\-]*\.tf$")
_RE_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.S)
_RE_RECURSO = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"', re.M)


class ArchivoNoPermitido(ValueError):
    pass


def extraer_archivos(texto: str) -> dict[str, str]:
    """Extrae los bloques <file name="x.tf">...</file>.

    Rechaza nombres con rutas o extensiones distintas de .tf para que el LLM
    no pueda escribir fuera del directorio de trabajo.
    """
    archivos: dict[str, str] = {}
    for nombre, contenido in _RE_ARCHIVO.findall(texto or ""):
        nombre = nombre.strip()
        if not _RE_NOMBRE_VALIDO.match(nombre):
            raise ArchivoNoPermitido(f"Nombre de archivo no permitido: {nombre!r}")
        cuerpo = contenido.strip("\n")
        m = _RE_FENCE.match(cuerpo)
        if m:
            cuerpo = m.group(1)
        archivos[nombre] = cuerpo.strip() + "\n"
    return archivos


def extraer_json(texto: str) -> dict:
    """Obtiene el primer objeto JSON de la respuesta, tolerando cercas de código."""
    texto = (texto or "").strip()
    m = _RE_FENCE.match(texto)
    if m:
        texto = m.group(1).strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin <= inicio:
        raise ValueError("La respuesta no contiene un objeto JSON")
    return json.loads(texto[inicio:fin + 1])


def recursos_en_codigo(archivos: dict[str, str]) -> list[tuple[str, str, str, int]]:
    """Lista (tipo, nombre, archivo, línea) de cada bloque resource."""
    salida = []
    for archivo, texto in archivos.items():
        for m in _RE_RECURSO.finditer(texto):
            salida.append((m.group(1), m.group(2), archivo, texto.count("\n", 0, m.start()) + 1))
    return salida


def tipos_en_codigo(archivos: dict[str, str]) -> list[str]:
    vistos: list[str] = []
    for tipo, *_ in recursos_en_codigo(archivos):
        if tipo not in vistos:
            vistos.append(tipo)
    return vistos


def recurso_en_linea(archivos: dict[str, str], archivo: str | None, linea: int | None) -> str | None:
    """Tipo del bloque resource que contiene esa línea (el último que empieza antes)."""
    if not archivo or not linea or archivo not in archivos:
        return None
    candidato = None
    for tipo, _, arch, ini in recursos_en_codigo({archivo: archivos[archivo]}):
        if ini <= linea:
            candidato = tipo
    return candidato


def cobertura(esperados: list[str], presentes: list[str]) -> float | None:
    if not esperados:
        return None
    esp = set(esperados)
    return round(len(esp & set(presentes)) / len(esp), 4)


def formatear_archivos(archivos: dict[str, str]) -> str:
    return "\n".join(f'<file name="{n}">\n{c.rstrip()}\n</file>' for n, c in archivos.items())
