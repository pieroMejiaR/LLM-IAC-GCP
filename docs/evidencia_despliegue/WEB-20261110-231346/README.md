# Despliegue real del caso de la picantería (10 de noviembre de 2026)

Evidencia del despliegue hecho desde el chat de la interfaz web (`web --despliegue`, puerto 8788) en el
proyecto `agente-iac-pm`, región `southamerica-west1`. Los archivos son los que escribió el agente, sin
editar. No se incluye el estado de Terraform, que contiene secretos.

## Qué se hizo

1. La persona escribió el requerimiento en lenguaje simple y adjuntó la carpeta de la aplicación
   (`picanteria-digital`). La ficha técnica se leyó de esa carpeta (`agent/proyecto.py`).
2. El agente propuso la especificación (8 piezas: una base de datos, tres secretos, dos cuentas de servicio
   y dos servicios de Cloud Run), que se aprobó sin cambios (revisión A).
3. Generó el Terraform, que pasó todos los controles en el primer intento, incluido `plan` con la cuenta
   Viewer: 0 reparaciones, 13 hallazgos de Checkov informativos, 71,2 s y 0,0354 USD de LLM (`resultado.json`).
4. Con las dos contraseñas escritas por la persona, calculó el plan con la cuenta `agente-deploy`:
   17 recursos por crear. Se aprobó escribiendo el ID del proyecto.

## Intentos

| Intento | Plan | Apply | Resultado |
| --- | --- | --- | --- |
| 1 (`intento-1/`) | 17 por crear, 2,97 s | 330,85 s | Falló al crear el servicio `api`: `Permission 'artifactregistry.repositories.downloadArtifacts' denied`. Quedaron creados 13 recursos (Cloud SQL, secretos, cuentas de servicio y permisos). |
| 2 (`intento-2/`) | 4 por crear, 6,53 s | 58,89 s | Desplegado. Prueba HTTP a `service_url`: código 200 en 513 ms, primer intento. |

La causa del fallo fue de preparación del proyecto, no del código generado: la cuenta de despliegue no
tenía `roles/artifactregistry.reader` sobre el repositorio de imágenes, y Cloud Run comprueba que quien
despliega pueda leer la imagen. Se otorgó el rol sobre el repositorio `apps`, se conservaron los recursos
ya creados y el segundo intento completó el despliegue sobre el mismo estado, con una nueva aprobación.

## Resultado

- Web: https://web-y2zz3fattq-tl.a.run.app (salida `service_url`)
- API: https://api-y2zz3fattq-tl.a.run.app
- Cloud SQL `picanteria-db`: PostgreSQL 17, `db-f1-micro`, edición ENTERPRISE, IP pública sin redes autorizadas.
- Comprobación posterior desde otra máquina: la web y la ruta `/ingresar` responden 200, y `/api/auth/me`
  responde 401 con el mensaje de la aplicación a través de la web, lo que confirma que la API arrancó,
  aplicó las migraciones y está conectada a la base de datos.
- Los recursos se conservaron (segunda confirmación vacía). Para destruirlos:
  `docker compose run --rm despliegue destruir resultados/web/WEB-20261110-231346/T1-a55e30733e32/despliegue`

## Archivos

- `resultado.json`: traza de la corrida del agente (llamadas al LLM, tokens, costo, controles).
- `terraform/`: el código generado y desplegado.
- `intento-1/` e `intento-2/`: `plan.txt`, `apply.log`, `resultado_despliegue.json` y, en el segundo, `http.txt`.
- Capturas de cada etapa: `docs/figuras/capturas/`.
