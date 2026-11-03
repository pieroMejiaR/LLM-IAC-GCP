"""Catálogo de servicios y paso Planificar (determinista, sin LLM)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from agent.schemas import Especificacion, PlanArchivos

RUTA_CATALOGO = Path(__file__).with_name("catalog.yaml")
ARCHIVOS = ["versions.tf", "providers.tf", "main.tf", "variables.tf", "outputs.tf"]


class ServicioCatalogo(BaseModel):
    descripcion: str
    recursos: list[str]
    si_publico: list[str] = Field(default_factory=list)
    si_privado: list[str] = Field(default_factory=list)
    requiere_si_privado: list[str] = Field(default_factory=list)
    notas: list[str] = Field(default_factory=list)


def _unicos(items: list[str]) -> list[str]:
    vistos: list[str] = []
    for i in items:
        if i not in vistos:
            vistos.append(i)
    return vistos


class Catalogo:
    def __init__(self, servicios: dict[str, ServicioCatalogo]) -> None:
        self.servicios = servicios

    @classmethod
    def cargar(cls, ruta: Path | str = RUTA_CATALOGO) -> "Catalogo":
        datos = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
        return cls({k: ServicioCatalogo(**v) for k, v in datos["servicios"].items()})

    @property
    def tipos_servicio(self) -> list[str]:
        return list(self.servicios)

    @property
    def tipos_permitidos(self) -> set[str]:
        tipos: set[str] = set()
        for s in self.servicios.values():
            tipos.update(s.recursos, s.si_publico, s.si_privado)
        return tipos

    def descripcion_servicios(self) -> str:
        return "\n".join(f"- {k}: {v.descripcion}" for k, v in self.servicios.items())

    def planificar(self, spec: Especificacion) -> PlanArchivos:
        por_servicio: dict[str, list[str]] = {}
        implicitos: list[str] = []
        presentes = {s.tipo for s in spec.servicios}
        for s in spec.servicios:
            cat = self.servicios[s.tipo]
            tipos = list(cat.recursos)
            if s.publico:
                tipos += cat.si_publico
            privado = not bool(s.parametros.get("ip_publica", False))
            if cat.si_privado and privado:
                tipos += cat.si_privado
                for req in cat.requiere_si_privado:
                    if req not in presentes and req not in implicitos:
                        implicitos.append(req)
            por_servicio[f"{s.tipo}:{s.nombre}"] = _unicos(tipos)
        for req in implicitos:
            por_servicio[f"{req}:implicito"] = list(self.servicios[req].recursos[:2])
        todos = _unicos([t for ts in por_servicio.values() for t in ts])
        return PlanArchivos(tipos_recurso=todos, archivos=list(ARCHIVOS), por_servicio=por_servicio,
                            servicios_implicitos=implicitos)

    def notas_para(self, plan: PlanArchivos) -> str:
        tipos = {clave.split(":")[0] for clave in plan.por_servicio}
        lineas = []
        for t in self.tipos_servicio:
            if t in tipos:
                lineas.append(f"{t}:")
                lineas += [f"  - {n}" for n in self.servicios[t].notas]
        return "\n".join(lineas)
