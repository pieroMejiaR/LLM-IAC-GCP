"""Línea de comandos del agente.

    python -m agent generar eval/scenarios/BAS-01.yaml --tratamiento T1
    python -m agent desplegar casos/caso_real.yaml
    python -m agent destruir resultados/despliegue/<run_id>/terraform
"""
from __future__ import annotations

import json
from pathlib import Path

import typer

from agent.config import Config
from agent.llm import ClienteLLM
from agent.pipeline import Agente
from agent.schemas import Escenario, ResultadoCorrida
from grounding.catalog import Catalogo
from grounding.schema_index import IndiceEsquema

app = typer.Typer(add_completion=False, help="Agente LLM que genera Terraform validado para GCP.")


def _componentes(cfg: Config, simulado: bool, sin_plan: bool) -> Agente:
    llm = ClienteLLM(cfg.modelo, cfg.tarifa_entrada_usd_m, cfg.tarifa_salida_usd_m,
                     max_tokens=cfg.max_tokens, temperatura=cfg.temperatura)
    if simulado:
        from sandbox.fake import ValidadorSimulado
        validador = ValidadorSimulado()
    else:
        from sandbox.local_runner import ValidadorLocal
        validador = ValidadorLocal(cfg.proyecto_gcp, cfg.region, cfg.cuenta_evaluacion,
                                   checkov_bloqueantes=set(cfg.checkov_bloqueantes),
                                   tflint_config=cfg.tflint_config, ejecutar_plan=not sin_plan)
    esquema = None
    if cfg.esquema_proveedor and Path(cfg.esquema_proveedor).exists():
        esquema = IndiceEsquema.cargar(cfg.esquema_proveedor)
    else:
        typer.secho(f"Aviso: no se encontró el esquema del proveedor ({cfg.esquema_proveedor}); "
                    "el grounding usará solo el catálogo.", fg=typer.colors.YELLOW, err=True)
    return Agente(llm, validador, Catalogo.cargar(), cfg, esquema)


def _guardar(res: ResultadoCorrida, salida: Path) -> Path:
    d = salida / res.escenario_id / f"{res.tratamiento}-{res.run_id}"
    (d / "terraform").mkdir(parents=True, exist_ok=True)
    for nombre, contenido in res.archivos.items():
        (d / "terraform" / nombre).write_text(contenido, encoding="utf-8")
    (d / "resultado.json").write_text(res.model_dump_json(indent=2), encoding="utf-8")
    return d


def _resumen(res: ResultadoCorrida) -> str:
    control_plan = res.validacion.control("plan") if res.validacion else None
    plan = "ok" if res.plan_ok else ("omitido" if control_plan and control_plan.omitido else "falla")
    return (f"validate={'ok' if res.validate_ok else 'falla'}  plan={plan}  "
            f"reparaciones={res.intentos}  checkov={res.checkov_fallidas}  tiempo={res.tiempo_s:.1f}s  "
            f"tokens={res.tokens_entrada}+{res.tokens_salida}  costo=${res.costo_usd:.4f}"
            + (f"\nerror ({res.tipo_error}): {res.error_final}" if res.error_final else ""))


@app.command()
def generar(
    escenario: Path = typer.Argument(..., exists=True, help="Archivo YAML del escenario"),
    tratamiento: str = typer.Option("T1", help="T1 agente completo, T0 línea base"),
    salida: Path = typer.Option(Path("resultados"), help="Carpeta de resultados"),
    simulado: bool = typer.Option(False, help="Validador simulado (sin Terraform), para probar el LLM"),
    sin_plan: bool = typer.Option(False, help="No ejecutar terraform plan"),
) -> None:
    """Genera y valida el Terraform de un escenario en modo evaluación (sin desplegar)."""
    if tratamiento not in ("T0", "T1"):
        raise typer.BadParameter("tratamiento debe ser T0 o T1")
    cfg = Config.desde_entorno()
    agente = _componentes(cfg, simulado, sin_plan)
    res = agente.correr(Escenario.desde_yaml(escenario), tratamiento)  # type: ignore[arg-type]
    d = _guardar(res, salida)
    typer.echo(_resumen(res))
    typer.echo(f"Archivos y traza en {d}")


@app.command()
def desplegar(
    escenario: Path = typer.Argument(..., exists=True, help="Archivo YAML del caso real"),
    salida: Path = typer.Option(Path("resultados/despliegue"), help="Carpeta de evidencia"),
) -> None:
    """Genera, valida y despliega un caso en GCP. Pide aprobación antes de apply y de destroy."""
    from agent.deploy import ejecutar_despliegue
    from sandbox.local_runner import DesplegadorLocal

    cfg = Config.desde_entorno()
    if not (cfg.proyecto_gcp and cfg.cuenta_despliegue):
        raise typer.BadParameter("Defina GCP_PROYECTO y GCP_CUENTA_DESPLIEGUE en el entorno o en .env")
    agente = _componentes(cfg, simulado=False, sin_plan=False)
    res = agente.correr(Escenario.desde_yaml(escenario), "T1")
    d = _guardar(res, salida)
    typer.echo(_resumen(res))
    if not (res.validate_ok and res.plan_ok and res.validacion and res.validacion.ok):
        typer.secho("El código no pasó todos los controles en modo evaluación; no se despliega.", fg=typer.colors.RED)
        raise typer.Exit(1)

    desplegador = DesplegadorLocal(cfg.proyecto_gcp, cfg.region, cfg.cuenta_despliegue, d / "despliegue")
    resultado = ejecutar_despliegue(
        res.archivos, desplegador,
        proyecto_id=cfg.proyecto_gcp,
        tipos_permitidos=agente.catalogo.tipos_permitidos,
        confirmar=lambda msg: typer.prompt(msg, default="", show_default=False),
        dir_evidencia=d / "evidencia",
    )
    typer.echo(json.dumps({"estado": resultado.estado, "motivo": resultado.motivo,
                           "prueba_http": resultado.prueba_http, "duraciones_s": resultado.duraciones_s},
                          indent=2, ensure_ascii=False))


@app.command()
def destruir(directorio: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Destruye los recursos de un despliegue previo (pide confirmación)."""
    from sandbox.local_runner import DesplegadorLocal

    cfg = Config.desde_entorno()
    if not (cfg.proyecto_gcp and cfg.cuenta_despliegue):
        raise typer.BadParameter("Defina GCP_PROYECTO y GCP_CUENTA_DESPLIEGUE")
    respuesta = typer.prompt(f"Escriba el ID del proyecto ({cfg.proyecto_gcp}) para destruir", default="",
                             show_default=False)
    if respuesta.strip() != cfg.proyecto_gcp:
        typer.echo("Cancelado.")
        raise typer.Exit(1)
    r = DesplegadorLocal(cfg.proyecto_gcp, cfg.region, cfg.cuenta_despliegue, directorio).destruir(str(directorio))
    (directorio / "destroy.log").write_text(r.salida, encoding="utf-8")
    typer.echo("Destruido." if r.ok else f"Falló destroy: {'; '.join(d.texto() for d in r.errores)}")
