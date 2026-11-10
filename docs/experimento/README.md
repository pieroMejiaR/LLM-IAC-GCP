# Datos del experimento

Copia de los resultados que respaldan la sección 5 del informe. La carpeta `resultados/` no se sube al
repositorio; aquí quedan los archivos necesarios para recalcular cada cifra.

| Carpeta | Lote | Código | Uso en el informe |
| --- | --- | --- | --- |
| `lote-v2/` | Experimento, 72 corridas, 10/11/2026 | `v1.1-experimento` (`0bc4930`) | Secciones 5.1 a 5.5 y conclusiones C2 y C3 |
| `lote-v1/` | Piloto, 72 corridas, 10/11/2026 | `v1.0-piloto` (`4a78d23`) | Solo la sección 5.1 y la Tabla 5.8 (no entra en el contraste de hipótesis) |

Archivos:

- `runs.csv`: una fila por corrida, con el formato del Anexo C.
- `lote-*.json`: configuración del lote (semilla 20261105, modelo, escenarios y orden de las corridas).
- `analisis.md`, `analisis.json` y `por_escenario.csv` (solo `lote-v2/`): salida de `eval/analysis.py`.

Para recalcular el análisis:

```bash
mkdir -p resultados/recalculo && cp docs/experimento/lote-v2/runs.csv resultados/recalculo/
docker compose run --rm --entrypoint python agente -m eval.analysis resultados/recalculo/runs.csv
```

El contenedor no monta `docs/`, por eso el CSV se copia antes a `resultados/`.

El piloto reveló que `terraform plan` fallaba por falta de valores en las variables sensibles y que el bucle
de T1 lo superaba escribiendo contraseñas por defecto. Las trazas completas de cada corrida (`resultado.json`
y los `.tf`) están en `resultados/lote-v1/` y `resultados/lote-v2/` de la máquina donde se ejecutaron.
