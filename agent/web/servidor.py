"""Interfaz web (chat) del agente.

    docker compose up -d web-puente               # modo evaluación: http://127.0.0.1:8787
    docker compose up -d web-puente-despliegue    # con despliegue:  http://127.0.0.1:8788

La persona escribe el requerimiento, revisa la especificación (la aprueba o pide cambios) y el
agente planifica, genera, valida y repara el Terraform con el mismo bucle T1 del experimento.

En modo evaluación (`web`) nunca despliega: no recibe un desplegador y no existe ningún camino
hacia apply. Con `web --despliegue`, el código que pasó todos los controles se puede desplegar
desde el chat con el flujo de agent.deploy, sin cambios en sus controles: plan guardado con la
cuenta de despliegue, rechazo de recursos fuera del catálogo (S8), aprobación escribiendo el ID
del proyecto (S7), apply de ese plan, prueba HTTP y destrucción con una segunda confirmación.
Las contraseñas que pide el plan solo viven en memoria.

Usa solo la biblioteca estándar, para no tocar las dependencias fijadas con hashes. Las
operaciones largas corren en un hilo y el navegador consulta el estado cada segundo. Hay un
solo trabajo con el LLM a la vez, para que un doble clic no gaste dos veces.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from agent import proyecto
from agent.deploy import ResultadoDespliegue, ejecutar_despliegue
from agent.pipeline import Agente
from agent.schemas import Escenario, ResultadoCorrida
from sandbox.contract import Desplegador
from sandbox.guard import variables_sin_valor

ESPERA_CONFIRMACION_S = 30 * 60     # sin respuesta en ese tiempo: no se aplica (o se conservan los recursos)
MAX_VALOR_VARIABLE = 256
MAX_PLAN_TEXTO = 60_000
_VARIABLES_DEL_SANDBOX = ("project_id", "region")
MAX_REQUERIMIENTO = 4000
MAX_CORRECCION = 2000
MAX_CUERPO = 64 * 1024
MAX_CUERPO_PROYECTO = 700 * 1024    # los archivos de configuración de la carpeta de la aplicación
MAX_SESIONES = 20
REGIONES = ("southamerica-west1", "southamerica-east1", "us-central1", "us-east1", "europe-west1")
HOSTS_LOCALES = {"127.0.0.1", "localhost", "::1"}
ESTATICOS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
CABECERAS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
_ID = r"([0-9a-f]{16})"


class ErrorPeticion(Exception):
    def __init__(self, estado: HTTPStatus, mensaje: str) -> None:
        super().__init__(mensaje)
        self.estado = estado
        self.mensaje = mensaje


@dataclass
class Despliegue:
    """Un despliegue iniciado desde el chat. El flujo y sus controles son los de agent.deploy."""
    evidencia: str
    res: ResultadoDespliegue = field(default_factory=ResultadoDespliegue)
    # planificando, esperando_aprobacion, aplicando, esperando_destruccion, destruyendo, terminado
    fase: str = "planificando"
    pregunta: str | None = None
    respuesta: str = ""
    evento: threading.Event = field(default_factory=threading.Event)
    desde: float = field(default_factory=time.time)

    def pasar_a(self, fase: str) -> None:
        self.fase, self.desde = fase, time.time()


@dataclass
class Sesion:
    id: str
    escenario: Escenario
    res: ResultadoCorrida
    despliegue: Despliegue | None = None
    requerimiento_usuario: str = ""     # lo que escribió la persona, sin la ficha
    ficha: dict | None = None           # ficha técnica leída de la carpeta de su aplicación
    estado: str = "analizando"      # analizando, revision, generando, terminado, error
    error: str | None = None        # error que termina la sesión
    aviso: str | None = None        # error recuperable (una corrección que no se pudo aplicar)
    correcciones: list[str] = field(default_factory=list)
    eventos: list[dict] = field(default_factory=list)   # la conversación, en orden
    rondas: int = 0                 # especificaciones propuestas (revisión A, B, C...)
    carpeta: str | None = None
    creada: float = field(default_factory=time.time)


class Aplicacion:
    def __init__(self, agente: Agente, salida: Path, info: dict,
                 guardar: Callable[[ResultadoCorrida, Path], Path], *,
                 desplegadores: Callable[[Path, dict[str, str]], Desplegador] | None = None,
                 proyecto_id: str | None = None,
                 probar: Callable[[str], dict] | None = None,
                 espera_confirmacion_s: float = ESPERA_CONFIRMACION_S) -> None:
        self.agente = agente
        self.salida = Path(salida)
        self.info = info
        self.guardar = guardar
        # Sin `desplegadores` (modo evaluación) no existe ningún camino hacia apply.
        self.desplegadores = desplegadores if proyecto_id else None
        self.proyecto_id = proyecto_id
        self.probar = probar
        self.espera_confirmacion_s = espera_confirmacion_s
        self.sesiones: dict[str, Sesion] = {}
        self.fichas: dict[str, dict] = {}     # fichas de proyecto recién leídas, por id
        self.ocupado = threading.Lock()   # un trabajo con el LLM a la vez
        self.desplegando = threading.Lock()   # un despliegue a la vez
        self.mutex = threading.Lock()     # protege el diccionario de sesiones

    # ------------------------------------------------------------------ acciones

    def leer_proyecto(self, cuerpo: dict) -> dict:
        """Arma la ficha técnica con los archivos que el navegador leyó de la carpeta de la aplicación.

        No usa el modelo ni guarda los archivos: solo queda la ficha, sin valores secretos.
        """
        lista = cuerpo.get("archivos")
        if not isinstance(lista, list) or not lista:
            raise ErrorPeticion(HTTPStatus.BAD_REQUEST, "No llegó ningún archivo del proyecto.")
        archivos = {}
        for a in lista:
            if not (isinstance(a, dict) and isinstance(a.get("ruta"), str) and isinstance(a.get("contenido"), str)):
                raise ErrorPeticion(HTTPStatus.BAD_REQUEST, "Formato de archivos no válido.")
            archivos[a["ruta"]] = a["contenido"]
        entradas = [str(e)[:80] for e in cuerpo.get("entradas") or [] if isinstance(e, str)][:200]
        try:
            ficha = proyecto.analizar(archivos, raiz=str(cuerpo.get("raiz") or "aplicacion"), entradas_raiz=entradas,
                                      repositorio=self.info.get("repositorio"))
        except proyecto.ProyectoInvalido as e:
            raise ErrorPeticion(HTTPStatus.BAD_REQUEST, str(e)) from None
        ficha["id"] = secrets.token_hex(8)
        with self.mutex:
            self.fichas[ficha["id"]] = ficha
            for viejo in list(self.fichas)[:-MAX_SESIONES]:
                del self.fichas[viejo]
        return ficha

    def crear(self, requerimiento: str, region: str, ficha_id: str | None = None) -> Sesion:
        requerimiento = (requerimiento or "").strip()
        ficha = None
        if ficha_id:
            with self.mutex:
                ficha = self.fichas.get(ficha_id)
            if ficha is None:
                raise ErrorPeticion(HTTPStatus.BAD_REQUEST, "La ficha del proyecto ya no está disponible; vuelva a elegir la carpeta.")
        if len(requerimiento) < 20:
            raise ErrorPeticion(HTTPStatus.BAD_REQUEST, "Describa el requerimiento con al menos 20 caracteres.")
        if len(requerimiento) > MAX_REQUERIMIENTO:
            raise ErrorPeticion(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                f"El requerimiento supera los {MAX_REQUERIMIENTO} caracteres.")
        if region not in REGIONES:
            raise ErrorPeticion(HTTPStatus.BAD_REQUEST, "Región no admitida.")
        ahora = datetime.now()
        titulo = " ".join(requerimiento.split())[:80]
        # La ficha acompaña al requerimiento como texto: el prompt de analizar es el mismo del experimento.
        completo = requerimiento + ("\n\n" + ficha["texto"] if ficha else "")
        esc = Escenario(id=f"WEB-{ahora:%Y%m%d-%H%M%S}", nivel="interactivo", titulo=titulo,
                        requerimiento=completo, contexto={"region": region, "entornos": ["dev"]})
        sesion = Sesion(id=secrets.token_hex(8), escenario=esc, res=self.agente.iniciar(esc, "T1"),
                        requerimiento_usuario=requerimiento, ficha=ficha)
        sesion.eventos.append({"autor": "usted", "tipo": "requerimiento", "texto": requerimiento, "region": region,
                               "proyecto": ficha["raiz"] if ficha else None})
        self._lanzar(sesion, "analizando", lambda: self._analizar(sesion, None))
        with self.mutex:
            self.sesiones[sesion.id] = sesion
            self._podar()
        return sesion

    def corregir(self, sid: str, texto: str) -> Sesion:
        sesion = self.obtener(sid)
        texto = (texto or "").strip()
        if sesion.estado != "revision":
            raise ErrorPeticion(HTTPStatus.CONFLICT, "La especificación no está en revisión.")
        if not texto:
            raise ErrorPeticion(HTTPStatus.BAD_REQUEST, "Escriba qué quiere cambiar.")
        if len(texto) > MAX_CORRECCION:
            raise ErrorPeticion(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                f"La corrección supera los {MAX_CORRECCION} caracteres.")
        sesion.correcciones.append(texto)
        sesion.eventos.append({"autor": "usted", "tipo": "correccion", "texto": texto})
        try:
            self._lanzar(sesion, "analizando", lambda: self._analizar(sesion, texto))
        except ErrorPeticion:
            sesion.correcciones.pop()
            sesion.eventos.pop()
            raise
        return sesion

    def aprobar(self, sid: str) -> Sesion:
        sesion = self.obtener(sid)
        if sesion.estado != "revision" or sesion.res.especificacion is None:
            raise ErrorPeticion(HTTPStatus.CONFLICT, "No hay una especificación lista para aprobar.")
        sesion.eventos.append({"autor": "usted", "tipo": "aprobacion", "revision": _letra(sesion.rondas)})
        try:
            self._lanzar(sesion, "generando", lambda: self._generar(sesion))
        except ErrorPeticion:
            sesion.eventos.pop()
            raise
        return sesion

    def obtener(self, sid: str) -> Sesion:
        with self.mutex:
            sesion = self.sesiones.get(sid)
        if sesion is None:
            raise ErrorPeticion(HTTPStatus.NOT_FOUND, "La sesión no existe o ya expiró.")
        return sesion

    # ------------------------------------------------------------------ trabajos en segundo plano

    def _lanzar(self, sesion: Sesion, estado: str, trabajo: Callable[[], None]) -> None:
        if not self.ocupado.acquire(blocking=False):
            raise ErrorPeticion(HTTPStatus.CONFLICT, "El agente está trabajando en otra solicitud; espere a que termine.")
        sesion.estado, sesion.aviso = estado, None

        def correr() -> None:
            try:
                trabajo()
            except Exception as e:  # noqa: BLE001 - cualquier fallo inesperado se muestra en la interfaz
                sesion.estado, sesion.error = "error", f"{type(e).__name__}: {e}"[:600]
                sesion.eventos.append({"autor": "agente", "tipo": "error", "texto": sesion.error})
            finally:
                self.ocupado.release()

        threading.Thread(target=correr, name=f"sesion-{sesion.id}", daemon=True).start()

    def _analizar(self, sesion: Sesion, correcciones: str | None) -> None:
        tenia_spec = sesion.res.especificacion is not None
        spec = self.agente.especificar(sesion.escenario, sesion.res, correcciones)
        if spec is not None:
            sesion.rondas += 1
            sesion.estado = "revision"
            sesion.eventos.append({"autor": "agente", "tipo": "especificacion", "revision": _letra(sesion.rondas)})
        elif tenia_spec:
            # La corrección falló: se conserva la especificación anterior para seguir revisando.
            sesion.estado, sesion.aviso = "revision", sesion.res.error_final
            sesion.eventos.append({"autor": "agente", "tipo": "aviso", "texto": sesion.aviso,
                                   "revision": _letra(sesion.rondas)})
        else:
            sesion.estado = "error"
            sesion.error = sesion.res.error_final or "No se obtuvo una especificación válida."
            sesion.eventos.append({"autor": "agente", "tipo": "error", "texto": sesion.error})
            sesion.carpeta = str(self.guardar(sesion.res, self.salida))

    def _generar(self, sesion: Sesion) -> None:
        self.agente.completar(sesion.escenario, sesion.res, sesion.res.especificacion)
        sesion.carpeta = str(self.guardar(sesion.res, self.salida))
        sesion.estado = "terminado"
        sesion.eventos.append({"autor": "agente", "tipo": "resultado"})

    # ------------------------------------------------------------------ despliegue (solo con --despliegue)

    def variables_requeridas(self, sesion: Sesion) -> list[str]:
        """Variables sin default (contraseñas, claves): quien despliega escribe su valor."""
        return [n for n in variables_sin_valor(sesion.res.archivos) if n not in _VARIABLES_DEL_SANDBOX]

    def _motivo_no_desplegable(self, sesion: Sesion) -> str | None:
        res = sesion.res
        if self.desplegadores is None:
            return "Esta interfaz está en modo evaluación y no despliega."
        if sesion.estado != "terminado" or not sesion.carpeta:
            return "Todavía no hay código validado."
        if not (res.validate_ok and res.validacion and res.validacion.ok):
            return "El código no pasó todos los controles; no se puede desplegar."
        if not res.plan_ok:
            return "El plan del modo evaluación no se ejecutó; no se puede desplegar sin esa verificación."
        d = sesion.despliegue
        if d is not None and d.fase != "terminado":
            return "Ya hay un despliegue en curso para este código."
        if d is not None and d.res.aplicado:
            return "Este código ya se aplicó una vez. Para desplegar de nuevo, inicie un requerimiento nuevo."
        return None

    def desplegar(self, sid: str, variables: object) -> Sesion:
        """Prepara el plan con la cuenta de despliegue. No aplica nada: eso exige la confirmación."""
        sesion = self.obtener(sid)
        if self.desplegadores is None:
            raise ErrorPeticion(HTTPStatus.FORBIDDEN, "Esta interfaz está en modo evaluación y no despliega.")
        motivo = self._motivo_no_desplegable(sesion)
        if motivo:
            raise ErrorPeticion(HTTPStatus.CONFLICT, motivo)
        valores: dict[str, str] = {}
        for nombre in self.variables_requeridas(sesion):
            valor = variables.get(nombre) if isinstance(variables, dict) else None
            if not isinstance(valor, str) or not valor:
                raise ErrorPeticion(HTTPStatus.BAD_REQUEST, f"Falta el valor de la variable {nombre}.")
            if len(valor) > MAX_VALOR_VARIABLE:
                raise ErrorPeticion(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"El valor de {nombre} es demasiado largo.")
            valores[nombre] = valor
        if not self.desplegando.acquire(blocking=False):
            raise ErrorPeticion(HTTPStatus.CONFLICT, "Hay otro despliegue en curso; espere a que termine.")

        carpeta = Path(sesion.carpeta)
        d = Despliegue(evidencia=str(carpeta / "evidencia"))
        sesion.despliegue = d
        sesion.eventos.append({"autor": "usted", "tipo": "despliegue_solicitado"})
        try:
            # Los valores quedan solo en memoria, dentro del desplegador; no se guardan ni se devuelven.
            desplegador = self.desplegadores(carpeta / "despliegue", valores)
        except Exception:
            self.desplegando.release()
            sesion.despliegue = None
            sesion.eventos.pop()
            raise
        preguntas = 0

        def confirmar(mensaje: str) -> str:
            nonlocal preguntas
            preguntas += 1
            d.evento.clear()
            d.respuesta, d.pregunta = "", mensaje
            if preguntas == 1:
                d.pasar_a("esperando_aprobacion")
                sesion.eventos.append({"autor": "agente", "tipo": "plan_listo", "recursos": len(d.res.cambios)})
            else:
                d.pasar_a("esperando_destruccion")
                sesion.eventos.append({"autor": "agente", "tipo": "pregunta_destruir", "estado": d.res.estado,
                                       "codigo": d.res.prueba_http.get("codigo")})
            respondio = d.evento.wait(self.espera_confirmacion_s)
            respuesta = d.respuesta if respondio else ""
            d.respuesta, d.pregunta = "", None
            if not respondio:   # sin respuesta a tiempo: equivale a no confirmar
                d.pasar_a("cerrando")
            return respuesta

        def correr() -> None:
            try:
                extra = {"probar": self.probar} if self.probar else {}
                ejecutar_despliegue(sesion.res.archivos, desplegador, proyecto_id=self.proyecto_id,
                                    tipos_permitidos=self.agente.catalogo.tipos_permitidos, confirmar=confirmar,
                                    dir_evidencia=carpeta / "evidencia", resultado=d.res, **extra)
            except Exception as e:  # noqa: BLE001 - se muestra en la interfaz
                d.res.estado, d.res.motivo = "error", f"{type(e).__name__}: {e}"[:600]
            finally:
                d.pregunta = None
                sesion.eventos.append({"autor": "agente", "tipo": "despliegue", "estado": d.res.estado,
                                       "motivo": d.res.motivo})
                d.pasar_a("terminado")
                self.desplegando.release()

        threading.Thread(target=correr, name=f"despliegue-{sesion.id}", daemon=True).start()
        return sesion

    def confirmar_despliegue(self, sid: str, texto: str) -> Sesion:
        """Entrega al flujo lo que escribió la persona. Quien decide si coincide es agent.deploy."""
        sesion = self.obtener(sid)
        d = sesion.despliegue
        texto = (texto or "").strip()[:200]
        coincide = texto == self.proyecto_id
        with self.mutex:
            if d is None or d.fase not in ("esperando_aprobacion", "esperando_destruccion"):
                raise ErrorPeticion(HTTPStatus.CONFLICT, "El despliegue no está esperando una confirmación.")
            sobre_plan = d.fase == "esperando_aprobacion"
            # La fase solo informa a la página; la decisión la toma agent.deploy al comparar el texto.
            d.pasar_a(("aplicando" if sobre_plan else "destruyendo") if coincide else "cerrando")
        sesion.eventos.append({"autor": "usted", "tipo": "confirmacion",
                               "sobre": "plan" if sobre_plan else "destruccion", "coincide": coincide})
        d.respuesta = texto
        d.evento.set()
        return sesion

    def _vista_despliegue(self, sesion: Sesion) -> dict:
        habilitado = self.desplegadores is not None
        motivo = self._motivo_no_desplegable(sesion)
        v: dict = {
            "habilitado": habilitado,
            "proyecto": self.proyecto_id if habilitado else None,
            "desplegable": motivo is None,
            "motivo": motivo,
            "variables": self.variables_requeridas(sesion) if habilitado and sesion.estado == "terminado" else [],
            "actual": None,
        }
        d = sesion.despliegue
        if d is not None:
            r = d.res
            actual = {
                "fase": d.fase, "segundos": round(time.time() - d.desde), "pregunta": d.pregunta,
                "estado": r.estado, "motivo": r.motivo, "cambios": list(r.cambios),
                "aprobado": r.aprobado, "aplicado": r.aplicado, "destruido": r.destruido,
                "prueba_http": dict(r.prueba_http), "duraciones_s": dict(r.duraciones_s),
                "directorio": r.directorio, "evidencia": d.evidencia,
            }
            if d.fase == "esperando_aprobacion":
                try:   # terraform show oculta los valores sensibles del plan
                    actual["plan_texto"] = (Path(d.evidencia) / "plan.txt").read_text(encoding="utf-8")[:MAX_PLAN_TEXTO]
                except OSError:
                    actual["plan_texto"] = ""
            v["actual"] = actual
        return v

    def _podar(self) -> None:
        if len(self.sesiones) <= MAX_SESIONES:
            return
        quietas = sorted((s for s in self.sesiones.values() if s.estado not in ("analizando", "generando")
                          and (s.despliegue is None or s.despliegue.fase == "terminado")),
                         key=lambda s: s.creada)
        for s in quietas[: len(self.sesiones) - MAX_SESIONES]:
            del self.sesiones[s.id]

    # ------------------------------------------------------------------ vistas

    def vista(self, sesion: Sesion) -> dict:
        res = sesion.res
        llamadas = [c.model_dump() for c in list(res.llamadas)]
        intentos = [i.model_dump() for i in list(res.intentos_detalle)]
        d = {
            "id": sesion.id,
            "escenario_id": sesion.escenario.id,
            "run_id": res.run_id,
            "estado": sesion.estado,
            "etapa": self._etapa(sesion, llamadas, intentos),
            "error": sesion.error,
            "aviso": sesion.aviso,
            "requerimiento": sesion.requerimiento_usuario or sesion.escenario.requerimiento,
            "proyecto": sesion.ficha,
            "region": sesion.escenario.contexto.get("region"),
            "correcciones": list(sesion.correcciones),
            "eventos": list(sesion.eventos),
            "rondas": sesion.rondas,
            "revision": _letra(sesion.rondas) if sesion.rondas else None,
            "especificacion": res.especificacion.model_dump() if res.especificacion else None,
            "plan_archivos": res.plan_archivos.model_dump() if res.plan_archivos else None,
            "llamadas": llamadas,
            "intentos": intentos,
            "errores_analisis": list(res.errores_analisis),
            "costo_usd": round(sum(c["costo_usd"] for c in llamadas), 6),
            "tokens_entrada": sum(c["tokens_entrada"] for c in llamadas),
            "tokens_salida": sum(c["tokens_salida"] for c in llamadas),
            "tiempo_s": round(res.tiempo_s, 1),
            "carpeta": sesion.carpeta,
            "resultado": self._resultado(res) if sesion.estado == "terminado" else None,
            "despliegue": self._vista_despliegue(sesion),
        }
        return d

    def _etapa(self, sesion: Sesion, llamadas: list[dict], intentos: list[dict]) -> str | None:
        if sesion.estado == "analizando":
            return "Revisando las correcciones" if sesion.correcciones and sesion.rondas else "Analizando el requerimiento"
        if sesion.estado != "generando":
            return None
        generadas = [c for c in llamadas if c["etapa"] in ("generar", "reparar")]
        if not generadas:
            return "Generando el Terraform"
        if len(intentos) < len(generadas):
            return "Validando con Terraform, TFLint y Checkov"
        return f"Reparando: intento {len(intentos)} de {self.agente.config.max_reparaciones}"

    @staticmethod
    def _resultado(res: ResultadoCorrida) -> dict:
        v = res.validacion
        controles = []
        checkov: list[dict] = []
        if v is not None:
            for c in v.controles:
                controles.append({
                    "control": c.control, "ok": c.ok, "omitido": c.omitido, "duracion_s": c.duracion_s,
                    "errores": [d.texto() for d in c.errores][:6],
                    "avisos": [d.mensaje for d in c.diagnosticos if d.severidad == "warning" and c.control != "checkov"][:3],
                })
            ck = v.control("checkov")
            if ck is not None:
                checkov = [{"regla": d.regla, "mensaje": d.mensaje, "archivo": d.archivo, "linea": d.linea,
                            "tipo_recurso": d.tipo_recurso, "severidad": d.severidad} for d in ck.diagnosticos]
        return {
            "ok": bool(v and v.ok),
            "validate_ok": res.validate_ok,
            "plan": res.estado_plan(),
            "reparaciones": res.intentos,
            "checkov_fallidas": res.checkov_fallidas,
            "checkov": checkov,
            "controles": controles,
            "archivos": dict(res.archivos),
            "tipos_recurso": list(res.tipos_recurso_codigo),
            "tipo_error": res.tipo_error,
            "error_final": res.error_final,
            "versiones": dict(res.versiones),
        }

    def zip_proyecto(self, sesion: Sesion) -> tuple[str, bytes]:
        if sesion.estado != "terminado" or not sesion.res.archivos:
            raise ErrorPeticion(HTTPStatus.CONFLICT, "Todavía no hay archivos para descargar.")
        carpeta = sesion.escenario.id.lower()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for nombre, contenido in sorted(sesion.res.archivos.items()):
                z.writestr(f"{carpeta}/{nombre}", contenido)
        return f"{carpeta}.zip", buf.getvalue()


def _letra(n: int) -> str:
    """Revisión de la especificación como en un plano: 1 = A, 2 = B..."""
    return chr(64 + n) if 1 <= n <= 26 else str(n)


# ---------------------------------------------------------------------- HTTP


def _estatico(nombre: str) -> bytes:
    return resources.files("agent.web").joinpath("estatico", nombre).read_bytes()


class Manejador(BaseHTTPRequestHandler):
    server_version = "agente-iac-web"
    sys_version = ""
    app: Aplicacion  # se asigna en crear_servidor

    def log_message(self, formato: str, *args) -> None:  # una línea por petición, sin la IP del cliente
        sys.stderr.write(f"[web] {self.command} {self.path.split('?', 1)[0]} -> {args[1] if len(args) > 1 else ''}\n")

    # ------------------------------------------------------------------ rutas

    def do_GET(self) -> None:  # noqa: N802
        self._atender(self._get)

    def do_POST(self) -> None:  # noqa: N802
        self._atender(self._post)

    def _get(self, ruta: str) -> None:
        if ruta in ESTATICOS:
            nombre, tipo = ESTATICOS[ruta]
            return self._enviar(HTTPStatus.OK, _estatico(nombre), tipo)
        if ruta == "/api/estado":
            return self._json(self.app.info)
        if m := re.fullmatch(rf"/api/sesiones/{_ID}", ruta):
            return self._json(self.app.vista(self.app.obtener(m.group(1))))
        if m := re.fullmatch(rf"/api/sesiones/{_ID}/proyecto\.zip", ruta):
            nombre, datos = self.app.zip_proyecto(self.app.obtener(m.group(1)))
            return self._enviar(HTTPStatus.OK, datos, "application/zip",
                                {"Content-Disposition": f'attachment; filename="{nombre}"'})
        raise ErrorPeticion(HTTPStatus.NOT_FOUND, "No encontrado.")

    def _post(self, ruta: str) -> None:
        cuerpo = self._cuerpo(MAX_CUERPO_PROYECTO if ruta == "/api/proyecto" else MAX_CUERPO)
        if ruta == "/api/proyecto":
            return self._json(self.app.leer_proyecto(cuerpo))
        if ruta == "/api/sesiones":
            ficha_id = cuerpo.get("proyecto")
            s = self.app.crear(str(cuerpo.get("requerimiento", "")),
                               str(cuerpo.get("region", self.app.info.get("region", REGIONES[0]))),
                               str(ficha_id) if ficha_id else None)
            return self._json(self.app.vista(s), HTTPStatus.ACCEPTED)
        if m := re.fullmatch(rf"/api/sesiones/{_ID}/correcciones", ruta):
            s = self.app.corregir(m.group(1), str(cuerpo.get("texto", "")))
            return self._json(self.app.vista(s), HTTPStatus.ACCEPTED)
        if m := re.fullmatch(rf"/api/sesiones/{_ID}/despliegue", ruta):
            return self._json(self.app.vista(self.app.desplegar(m.group(1), cuerpo.get("variables"))),
                              HTTPStatus.ACCEPTED)
        if m := re.fullmatch(rf"/api/sesiones/{_ID}/despliegue/confirmacion", ruta):
            s = self.app.confirmar_despliegue(m.group(1), str(cuerpo.get("texto", "")))
            return self._json(self.app.vista(s), HTTPStatus.ACCEPTED)
        if m := re.fullmatch(rf"/api/sesiones/{_ID}/aprobar", ruta):
            return self._json(self.app.vista(self.app.aprobar(m.group(1))), HTTPStatus.ACCEPTED)
        raise ErrorPeticion(HTTPStatus.NOT_FOUND, "No encontrado.")

    # ------------------------------------------------------------------ utilidades

    def _atender(self, manejar: Callable[[str], None]) -> None:
        try:
            self._verificar_origen()
            manejar(self.path.split("?", 1)[0])
        except ErrorPeticion as e:
            self._json({"error": e.mensaje}, e.estado)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"Error interno: {type(e).__name__}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            sys.stderr.write(f"[web] error interno: {e!r}\n")

    def _verificar_origen(self) -> None:
        """Solo se atiende a localhost (evita DNS rebinding) y a peticiones del mismo origen."""
        host = self.headers.get("Host", "")
        nombre = urlsplit(f"//{host}").hostname or ""
        if nombre not in HOSTS_LOCALES:
            raise ErrorPeticion(HTTPStatus.FORBIDDEN, "Solo se admite acceso desde localhost.")
        origen = self.headers.get("Origin")
        if origen and urlsplit(origen).netloc != host:
            raise ErrorPeticion(HTTPStatus.FORBIDDEN, "Origen no permitido.")

    def _cuerpo(self, maximo: int = MAX_CUERPO) -> dict:
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ErrorPeticion(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Se espera JSON.")
        try:
            largo = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ErrorPeticion(HTTPStatus.BAD_REQUEST, "Content-Length inválido.") from None
        if largo > maximo:
            raise ErrorPeticion(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Solicitud demasiado grande.")
        try:
            datos = json.loads(self.rfile.read(largo) or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise ErrorPeticion(HTTPStatus.BAD_REQUEST, "JSON inválido.") from None
        if not isinstance(datos, dict):
            raise ErrorPeticion(HTTPStatus.BAD_REQUEST, "Se espera un objeto JSON.")
        return datos

    def _json(self, datos: dict, estado: HTTPStatus = HTTPStatus.OK) -> None:
        self._enviar(estado, json.dumps(datos, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _enviar(self, estado: HTTPStatus, cuerpo: bytes, tipo: str, extra: dict | None = None) -> None:
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        for clave, valor in {**CABECERAS, **(extra or {})}.items():
            self.send_header(clave, valor)
        self.end_headers()
        self.wfile.write(cuerpo)


def crear_servidor(app: Aplicacion, host: str, puerto: int) -> ThreadingHTTPServer:
    manejador = type("ManejadorApp", (Manejador,), {"app": app})
    servidor = ThreadingHTTPServer((host, puerto), manejador)
    servidor.daemon_threads = True
    return servidor
