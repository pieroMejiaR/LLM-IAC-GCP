"""Reduce el esquema completo del proveedor a los tipos del catálogo.

Uso:
    python -m grounding.extraer_esquema esquema_completo.json grounding/esquema_catalogo.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from grounding.catalog import Catalogo
from grounding.schema_index import IndiceEsquema


def main(entrada: str, salida: str) -> None:
    indice = IndiceEsquema.cargar(entrada)
    tipos = sorted(Catalogo.cargar().tipos_permitidos)
    faltan = [t for t in tipos if not indice.tiene(t)]
    reducido = {"resource_schemas": {t: indice.esquemas[t] for t in tipos if indice.tiene(t)}}
    Path(salida).write_text(json.dumps(reducido, indent=1), encoding="utf-8")
    print(f"{len(reducido['resource_schemas'])} tipos escritos en {salida}")
    if faltan:
        print("Tipos del catálogo que no existen en el proveedor:", ", ".join(faltan))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
