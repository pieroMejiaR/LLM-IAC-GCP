# Evidencia de seguridad del sandbox (S1 a S8)

| Archivo | Prueba | Cómo se generó |
| --- | --- | --- |
| `pytest-contenedor-v1.1.txt` | S1 a S4, S6, S7 y S8 (130 pruebas) | `docker compose run --rm -T pruebas` sobre el código del experimento más el ingreso de contraseñas del modo despliegue (commit `b0aa708`), 2026-11-10 |
| `pytest-contenedor.txt` | S1 a S4, S6, S7 y S8 (110 pruebas) | `docker compose run --rm -T pruebas` sobre el código del piloto (`4a78d23`), 2026-11-09 |
| `S5-20261110-025243.txt` | S5: la cuenta Viewer no puede crear un bucket (4/4) | `docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad s5` en el proyecto `agente-iac-pm`, 2026-11-10 |
| `diagnostico-20261110-025158.txt` | Diagnóstico del entorno antes del experimento (10/10) | `docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad diagnostico`, 2026-11-10 |
| `S6-red.txt` | S6: el contenedor no sale a internet (10/10) | `docker compose run --rm --entrypoint python pruebas -m sandbox.pruebas_seguridad s6`, 2026-11-05 |
| `S6-log-proxy.txt` | S6: registro del proxy durante esa prueba | `docker compose logs --no-log-prefix proxy` |
| `versiones.txt` | Sección 4.1: versiones instaladas en la imagen | `python -m sandbox.pruebas_seguridad versiones`, 2026-11-05 |

Los archivos no contienen claves ni tokens.
