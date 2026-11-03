"""Índice del esquema del proveedor google para inyectar en los prompts.

Lee la salida de `terraform providers schema -json` (completa o reducida con
grounding/extraer_esquema.py) y resume cada tipo de recurso en texto compacto:
argumentos requeridos, opcionales y bloques anidados. Los atributos solo
calculados (computed) se omiten porque el LLM no debe escribirlos.
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_CARACTERES_POR_RECURSO = 3000


def _tipo(t) -> str:
    if isinstance(t, str):
        return t
    if isinstance(t, list) and t:
        if t[0] in ("list", "set", "map") and len(t) > 1:
            return f"{t[0]}({_tipo(t[1])})"
        if t[0] == "object":
            return "object"
        return str(t[0])
    return "any"


def _resumir_bloque(bloque: dict, nivel: int = 0) -> list[str]:
    attrs = bloque.get("attributes", {}) or {}
    req, opc = [], []
    for nombre, a in sorted(attrs.items()):
        if a.get("deprecated"):
            continue
        if a.get("required"):
            req.append(f"{nombre} ({_tipo(a.get('type'))})")
        elif a.get("optional"):
            opc.append(f"{nombre} ({_tipo(a.get('type'))})")
    sangria = "  " * (nivel + 1)
    lineas = []
    if req:
        lineas.append(f"{sangria}requeridos: {', '.join(req)}")
    if opc:
        lineas.append(f"{sangria}opcionales: {', '.join(opc)}")
    for nombre, bt in sorted((bloque.get("block_types") or {}).items()):
        interno = bt.get("block") or {}
        if interno.get("deprecated"):
            continue
        cardinalidad = []
        if bt.get("min_items"):
            cardinalidad.append(f"mín. {bt['min_items']}")
        if bt.get("max_items"):
            cardinalidad.append(f"máx. {bt['max_items']}")
        extra = f" [{', '.join(cardinalidad)}]" if cardinalidad else ""
        lineas.append(f"{sangria}bloque {nombre}{extra}:")
        if nivel < 2:
            lineas += _resumir_bloque(interno, nivel + 1)
    return lineas


class IndiceEsquema:
    def __init__(self, esquemas: dict[str, dict], version: str | None = None) -> None:
        self.esquemas = esquemas
        self.version = version

    @classmethod
    def cargar(cls, ruta: str | Path) -> "IndiceEsquema":
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
        if "provider_schemas" in datos:   # salida completa de terraform providers schema -json
            esquemas: dict[str, dict] = {}
            for fuente, prov in datos["provider_schemas"].items():
                if fuente.endswith("/google") or fuente.endswith("/google-beta"):
                    for tipo, sch in (prov.get("resource_schemas") or {}).items():
                        esquemas.setdefault(tipo, sch)
            return cls(esquemas)
        return cls(datos.get("resource_schemas", {}), datos.get("version"))

    def tiene(self, tipo: str) -> bool:
        return tipo in self.esquemas

    def resumen(self, tipo: str) -> str:
        sch = self.esquemas.get(tipo)
        if not sch:
            return f"{tipo}\n  (sin esquema disponible)"
        texto = "\n".join([tipo, *_resumir_bloque(sch.get("block", {}))])
        if len(texto) > MAX_CARACTERES_POR_RECURSO:
            texto = texto[:MAX_CARACTERES_POR_RECURSO] + "\n  ... (resumen truncado)"
        return texto

    def resumen_varios(self, tipos: list[str]) -> str:
        return "\n\n".join(self.resumen(t) for t in tipos)
