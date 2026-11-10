# Evidencia de seguridad del sandbox (S1 a S8)

| Archivo | Prueba | Cómo se generó |
| --- | --- | --- |
| `pytest-contenedor.txt` | S1 a S4, S6, S7 y S8 (110 pruebas) | `docker compose run --rm -T pruebas` sobre `main` (código congelado `4a78d23`), 2026-11-09 |
| `S6-red.txt` | S6: el contenedor no sale a internet (10/10) | `docker compose run --rm --entrypoint python pruebas -m sandbox.pruebas_seguridad s6`, 2026-11-05 |
| `S6-log-proxy.txt` | S6: registro del proxy durante esa prueba | `docker compose logs --no-log-prefix proxy` |
| `versiones.txt` | Sección 4.1: versiones instaladas en la imagen | `python -m sandbox.pruebas_seguridad versiones`, 2026-11-05 |
| `S5-viewer.txt` | S5: la cuenta Viewer no puede crear un bucket | **Pendiente.** Lo genera quien tiene las credenciales con `docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad s5` |

Los archivos no contienen claves ni tokens.
