"""Bucle del agente: analizar, planificar, generar, validar y reparar.

T1 (agente completo): análisis a especificación, plan determinista desde el
catálogo, generación con el esquema del proveedor en el contexto, validación
y hasta N reparaciones guiadas por los errores.

T0 (línea base): una sola llamada con el mismo modelo y las mismas reglas
base, sin especificación, sin esquema y sin reparación. Se valida una vez
solo para medir.
"""
from __future__ import annotations

import json
import time
import uuid

from pydantic import ValidationError

from agent import prompts
from agent.config import Config
from agent.llm import ClienteLLM, ErrorLLM, RespuestaLLM
from agent.parsing import (
    ArchivoNoPermitido,
    cobertura,
    extraer_archivos,
    extraer_json,
    formatear_archivos,
    recurso_en_linea,
    tipos_en_codigo,
)
from agent.schemas import (
    Escenario,
    Especificacion,
    Intento,
    LlamadaLLM,
    PlanArchivos,
    ResultadoCorrida,
    Tratamiento,
    errores_texto,
)
from grounding.catalog import Catalogo
from grounding.schema_index import IndiceEsquema
from sandbox.contract import Diagnostico, ResultadoControl, ResultadoValidacion, Validador


class ErrorEspecificacion(RuntimeError):
    pass


def _resultado_formato(archivos: dict[str, str], mensaje: str) -> ResultadoValidacion:
    d = Diagnostico(control="formato", mensaje=mensaje)
    return ResultadoValidacion(archivos=archivos, controles=[ResultadoControl(control="formato", ok=False, diagnosticos=[d])])


class Agente:
    def __init__(
        self,
        llm: ClienteLLM,
        validador: Validador,
        catalogo: Catalogo,
        config: Config,
        esquema: IndiceEsquema | None = None,
    ) -> None:
        self.llm = llm
        self.validador = validador
        self.catalogo = catalogo
        self.config = config
        self.esquema = esquema
        self.sistema = prompts.render("sistema")
        self.reglas = prompts.render("reglas", version_proveedor=config.version_proveedor, region=config.region)

    # ------------------------------------------------------------------ API pública

    def correr(self, escenario: Escenario, tratamiento: Tratamiento = "T1", *, repeticion: int = 1,
               semilla: int | None = None) -> ResultadoCorrida:
        """Corrida completa sin intervención humana (experimento y CLI)."""
        res = self.iniciar(escenario, tratamiento, repeticion=repeticion, semilla=semilla)
        spec: Especificacion | None = None
        if tratamiento == "T1":
            spec = self.especificar(escenario, res)
            if spec is None:
                self._cerrar(res, None, escenario)
                return res
        return self.completar(escenario, res, spec)

    # La interfaz web usa los mismos pasos por separado para que la persona revise la
    # especificación antes de generar: iniciar, especificar (una o más veces) y completar.

    def iniciar(self, escenario: Escenario, tratamiento: Tratamiento = "T1", *, repeticion: int = 1,
                semilla: int | None = None) -> ResultadoCorrida:
        return ResultadoCorrida(run_id=uuid.uuid4().hex[:12], escenario_id=escenario.id, nivel=escenario.nivel,
                                tratamiento=tratamiento, repeticion=repeticion, semilla=semilla,
                                modelo=self.llm.modelo)

    def especificar(self, escenario: Escenario, res: ResultadoCorrida,
                    correcciones: str | None = None) -> Especificacion | None:
        """Paso 1 de T1. Devuelve None si falla; el error queda en res."""
        t0 = time.perf_counter()
        try:
            spec = self._analizar(escenario, res, correcciones)
            res.especificacion = spec
            res.tipo_error, res.error_final = "ninguno", None
            return spec
        except ErrorLLM as e:
            res.tipo_error, res.error_final = "entorno", str(e)[:600]
        except ErrorEspecificacion as e:
            res.tipo_error, res.error_final = "codigo", str(e)[:600]
        finally:
            res.tiempo_s += time.perf_counter() - t0
        return None

    def completar(self, escenario: Escenario, res: ResultadoCorrida,
                  spec: Especificacion | None = None) -> ResultadoCorrida:
        """T1: planificar, generar, validar y reparar desde la especificación. T0: la llamada única."""
        t0 = time.perf_counter()
        validacion: ResultadoValidacion | None = None
        try:
            if res.tratamiento == "T1":
                if spec is None:
                    raise ValueError("T1 necesita una especificación")
                validacion = self._t1(escenario, res, spec)
            else:
                validacion = self._t0(escenario, res)
        except ErrorLLM as e:
            res.tipo_error, res.error_final = "entorno", str(e)[:600]
        except ErrorEspecificacion as e:
            res.tipo_error, res.error_final = "codigo", str(e)[:600]
        res.tiempo_s += time.perf_counter() - t0
        self._cerrar(res, validacion, escenario)
        return res

    # ------------------------------------------------------------------ tratamientos

    def _t0(self, esc: Escenario, res: ResultadoCorrida) -> ResultadoValidacion:
        usuario = prompts.render("linea_base", requerimiento=esc.requerimiento,
                                 contexto=json.dumps(esc.contexto, ensure_ascii=False), reglas=self.reglas)
        r = self._llamar("generar", usuario, res)
        archivos, error = self._archivos(r.texto)
        validacion = self._validar(archivos, error, res, numero=0, accion="generar")
        return validacion

    def _t1(self, esc: Escenario, res: ResultadoCorrida, spec: Especificacion) -> ResultadoValidacion:
        plan = self.catalogo.planificar(spec)
        res.plan_archivos = plan

        usuario = prompts.render(
            "generar",
            especificacion=spec.model_dump_json(indent=2),
            contexto=json.dumps(esc.contexto, ensure_ascii=False),
            plan=self._texto_plan(plan),
            esquema=self._esquema(plan.tipos_recurso),
            notas=self.catalogo.notas_para(plan),
            reglas=self.reglas,
        )
        r = self._llamar("generar", usuario, res)
        archivos, error = self._archivos(r.texto)
        validacion = self._validar(archivos, error, res, numero=0, accion="generar")

        reparaciones = 0
        while not validacion.ok and reparaciones < self.config.max_reparaciones:
            fallo = validacion.primer_fallo
            if fallo is not None and fallo.es_entorno:
                break  # no se repara lo que no es culpa del código
            reparaciones += 1
            validacion = self._reparar(validacion, plan, reparaciones, res)
        res.intentos = reparaciones
        return validacion

    # ------------------------------------------------------------------ pasos

    def _analizar(self, esc: Escenario, res: ResultadoCorrida, correcciones: str | None = None) -> Especificacion:
        requerimiento = esc.requerimiento
        if correcciones and correcciones.strip():
            # Revisión humana desde la interfaz web: la plantilla no cambia, el texto se agrega al requerimiento.
            if res.especificacion is not None:
                requerimiento += ("\n\nEspecificación propuesta antes:\n"
                                  + res.especificacion.model_dump_json(indent=2))
            requerimiento += ("\n\nCorrecciones de la persona usuaria sobre esa especificación (tienen prioridad):\n"
                              + correcciones.strip())
        usuario = prompts.render(
            "analizar",
            servicios=self.catalogo.descripcion_servicios(),
            requerimiento=requerimiento,
            contexto=json.dumps(esc.contexto, ensure_ascii=False),
            region=esc.contexto.get("region", self.config.region),
        )
        ultimo_error = ""
        for intento in range(2):
            texto = usuario if intento == 0 else (
                f"{usuario}\n\nTu respuesta anterior no cumplió el esquema: {ultimo_error}\nResponde de nuevo solo con el JSON corregido.")
            r = self._llamar("analizar", texto, res, json_mode=True)
            try:
                return Especificacion(**extraer_json(r.texto))
            except (ValueError, ValidationError, TypeError) as e:
                ultimo_error = str(e)[:800]
                res.errores_analisis.append(ultimo_error)
        raise ErrorEspecificacion(f"No se obtuvo una especificación válida: {ultimo_error}")

    def _reparar(self, validacion: ResultadoValidacion, plan: PlanArchivos, numero: int,
                 res: ResultadoCorrida) -> ResultadoValidacion:
        fallo = validacion.primer_fallo
        assert fallo is not None
        archivos = dict(validacion.archivos)
        afectados: list[str] = []
        for d in fallo.errores:
            tipo = d.tipo_recurso or recurso_en_linea(archivos, d.archivo, d.linea)
            if tipo and tipo not in afectados:
                afectados.append(tipo)
        if not afectados:
            afectados = plan.tipos_recurso
        usuario = prompts.render(
            "reparar",
            control=fallo.control,
            intento=numero,
            maximo=self.config.max_reparaciones,
            errores="\n".join(f"- {e}" for e in errores_texto(fallo.errores)) or "- (sin detalle)",
            esquema=self._esquema(afectados),
            archivos=formatear_archivos(archivos) if archivos else "(no hay archivos válidos todavía)",
            reglas=self.reglas,
        )
        r = self._llamar("reparar", usuario, res)
        nuevos, error = self._archivos(r.texto)
        if not error:
            archivos.update(nuevos)
        return self._validar(archivos, error, res, numero=numero, accion="reparar")

    # ------------------------------------------------------------------ utilidades

    def _llamar(self, etapa: str, usuario: str, res: ResultadoCorrida, *, json_mode: bool = False) -> RespuestaLLM:
        r = self.llm.completar(self.sistema, usuario, json_mode=json_mode)
        res.llamadas.append(LlamadaLLM(etapa=etapa, tokens_entrada=r.tokens_entrada, tokens_salida=r.tokens_salida,
                                       tokens_razonamiento=r.tokens_razonamiento, costo_usd=r.costo_usd,
                                       duracion_s=round(r.duracion_s, 3)))
        return r

    @staticmethod
    def _archivos(texto: str) -> tuple[dict[str, str], str | None]:
        try:
            archivos = extraer_archivos(texto)
        except ArchivoNoPermitido as e:
            return {}, str(e)
        if not archivos:
            return {}, 'La respuesta no contiene archivos en el formato <file name="...">...</file>.'
        return archivos, None

    def _validar(self, archivos: dict[str, str], error_formato: str | None, res: ResultadoCorrida,
                 *, numero: int, accion: str) -> ResultadoValidacion:
        t0 = time.perf_counter()
        if error_formato or not archivos:
            v = _resultado_formato(archivos, error_formato or "Sin archivos")
        else:
            v = self.validador.validar(archivos)
        fallo = v.primer_fallo
        res.intentos_detalle.append(Intento(
            numero=numero, accion=accion, ok=v.ok,
            control_fallido=fallo.control if fallo else None,
            errores=errores_texto(fallo.errores) if fallo else [],
            duracion_validacion_s=round(time.perf_counter() - t0, 3),
        ))
        return v

    def _esquema(self, tipos: list[str]) -> str:
        if not self.esquema:
            return "(esquema no disponible; usa la documentación vigente del proveedor google)"
        return self.esquema.resumen_varios(tipos)

    @staticmethod
    def _texto_plan(plan: PlanArchivos) -> str:
        lineas = [f"- {clave}: {', '.join(tipos)}" for clave, tipos in plan.por_servicio.items()]
        if plan.servicios_implicitos:
            lineas.append(f"(Servicios agregados por dependencia: {', '.join(plan.servicios_implicitos)})")
        return "\n".join(lineas)

    @staticmethod
    def _cerrar(res: ResultadoCorrida, v: ResultadoValidacion | None, esc: Escenario) -> None:
        res.tokens_entrada = sum(c.tokens_entrada for c in res.llamadas)
        res.tokens_salida = sum(c.tokens_salida for c in res.llamadas)
        res.costo_usd = sum(c.costo_usd for c in res.llamadas)
        if v is None:
            return
        res.validacion = v
        res.archivos = dict(v.archivos)
        res.versiones = dict(v.versiones)
        res.validate_ok = v.validate_ok
        res.plan_ok = v.plan_ok
        res.checkov_fallidas = v.checkov_fallidas
        res.tipos_recurso_codigo = tipos_en_codigo(v.archivos)
        res.tipos_recurso_plan = sorted({c.tipo for c in v.cambios_plan})
        res.cobertura_codigo = cobertura(esc.servicios_esperados, res.tipos_recurso_codigo)
        res.cobertura_plan = cobertura(esc.servicios_esperados, res.tipos_recurso_plan) if v.plan_ok else None
        fallo = v.primer_fallo
        if fallo and res.tipo_error == "ninguno":
            res.tipo_error = "entorno" if fallo.es_entorno else "codigo"
            res.error_final = "; ".join(errores_texto(fallo.errores, 3))[:600] or f"Falló {fallo.control}"
