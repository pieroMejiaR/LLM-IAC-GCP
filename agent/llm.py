"""Capa LLM: una sola función para llamar al modelo y contar tokens y costo.

Usa LiteLLM para no depender de un proveedor. El costo se calcula con las
tarifas de la configuración (USD por millón de tokens) para que sea
verificable contra la página de precios citada en el informe.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


_NO_REINTENTAR = {
    "AuthenticationError", "BadRequestError", "NotFoundError",
    "PermissionDeniedError", "ContextWindowExceededError",
}


class ErrorLLM(RuntimeError):
    """Falla del proveedor (red, cuota, autenticación). Se clasifica como error del entorno."""


@dataclass
class RespuestaLLM:
    texto: str
    tokens_entrada: int
    tokens_salida: int
    tokens_razonamiento: int | None
    costo_usd: float
    duracion_s: float


def _campo(obj, nombre, defecto=None):
    if obj is None:
        return defecto
    if isinstance(obj, dict):
        return obj.get(nombre, defecto)
    return getattr(obj, nombre, defecto)


class ClienteLLM:
    def __init__(
        self,
        modelo: str,
        tarifa_entrada_usd_m: float,
        tarifa_salida_usd_m: float,
        *,
        max_tokens: int = 32_000,
        temperatura: float | None = None,
        reintentos: int = 3,
        espera_s: float = 5.0,
        completion_fn: Callable | None = None,
    ) -> None:
        self.modelo = modelo
        self.tarifa_entrada = tarifa_entrada_usd_m
        self.tarifa_salida = tarifa_salida_usd_m
        self.max_tokens = max_tokens
        self.temperatura = temperatura
        self.reintentos = reintentos
        self.espera_s = espera_s
        self._completion = completion_fn

    def _fn(self) -> Callable:
        if self._completion is None:
            import litellm  # importación diferida: las pruebas no la necesitan
            self._completion = litellm.completion
        return self._completion

    def costo(self, tokens_entrada: int, tokens_salida: int) -> float:
        return (tokens_entrada * self.tarifa_entrada + tokens_salida * self.tarifa_salida) / 1_000_000

    def completar(self, sistema: str, usuario: str, *, json_mode: bool = False) -> RespuestaLLM:
        kwargs: dict = {
            "model": self.modelo,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            "max_tokens": self.max_tokens,
        }
        if self.temperatura is not None:
            kwargs["temperature"] = self.temperatura
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        ultimo_error: Exception | None = None
        for intento in range(self.reintentos):
            t0 = time.perf_counter()
            try:
                resp = self._fn()(**kwargs)
            except Exception as e:  # errores de red, cuota o autenticación del proveedor
                primera = (str(e).strip().splitlines() or [""])[0][:300]
                if type(e).__name__ in _NO_REINTENTAR or "api key" in primera.lower():
                    raise ErrorLLM(f"{type(e).__name__}: {primera}") from e
                ultimo_error = e
                if intento < self.reintentos - 1:
                    time.sleep(self.espera_s * (2 ** intento))
                continue
            dur = time.perf_counter() - t0
            choices = _campo(resp, "choices") or []
            mensaje = _campo(choices[0], "message") if choices else None
            texto = _campo(mensaje, "content") or ""
            uso = _campo(resp, "usage")
            entrada = int(_campo(uso, "prompt_tokens", 0) or 0)
            salida = int(_campo(uso, "completion_tokens", 0) or 0)
            detalle = _campo(uso, "completion_tokens_details")
            razonamiento = _campo(detalle, "reasoning_tokens")
            return RespuestaLLM(
                texto=texto,
                tokens_entrada=entrada,
                tokens_salida=salida,
                tokens_razonamiento=int(razonamiento) if razonamiento is not None else None,
                costo_usd=self.costo(entrada, salida),
                duracion_s=dur,
            )
        detalle = (str(ultimo_error).strip().splitlines() or [""])[0][:300]
        raise ErrorLLM(f"El proveedor del LLM falló {self.reintentos} veces: {type(ultimo_error).__name__}: {detalle}")
