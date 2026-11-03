Convierte el siguiente requerimiento en una especificación JSON para generar infraestructura en Google Cloud.

Tipos de servicio permitidos (campo "tipo"):
$servicios

Requerimiento:
<<<
$requerimiento
>>>

Contexto adicional (JSON): $contexto

Responde solo con un objeto JSON con esta forma:
{"proyecto": "nombre-corto", "region": "$region", "entornos": ["dev"], "servicios": [{"tipo": "cloud_run", "nombre": "api", "publico": true, "parametros": {}}], "restricciones": ["..."], "supuestos": ["..."]}

Reglas:
- Usa solo tipos de la lista. Si el requerimiento pide algo fuera de la lista, no lo incluyas y anótalo en "supuestos".
- "nombre" va en minúsculas, con letras, números y guiones, empieza por letra y tiene como máximo 40 caracteres.
- Si falta un dato (región, tamaño, exposición pública), elige la opción más segura y más barata y regístrala en "supuestos".
- Para cloud_sql_postgres indica en "parametros" la clave "ip_publica" (true o false); por defecto false.
- Copia en "restricciones" las condiciones explícitas del requerimiento (por ejemplo, "la base de datos no tiene IP pública").
- No agregues servicios que el requerimiento no pide.
