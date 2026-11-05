"""Revisión estática previa a terraform init (pruebas S2 a S4).

El código lo escribe un LLM, así que se trata como no confiable. Antes de que
Terraform lo toque, esta revisión rechaza lo que podría ejecutar programas
locales, leer archivos o credenciales del sandbox, hablar con otros servicios,
cambiar la cuenta con la que corre plan o usar otro proveedor.

No busca patrones sobre el texto crudo. Un analizador léxico separa
comentarios, cadenas y heredocs, y un analizador de bloques recorre la
estructura del HCL. Así no se esquiva con etiquetas sin comillas
(`data external x {}`), con bloques anidados ni con comentarios, y lo que está
dentro de un comentario o de una cadena no produce falsos positivos.

Falla cerrada: si un archivo no se puede analizar (cadena, comentario o bloque
sin cerrar), se rechaza.

Capas que siguen a esta (defensa en profundidad):
- En el contenedor, Terraform solo instala google y google-beta desde un espejo
  local, sin acceso al registro.
- La red del contenedor solo llega a *.googleapis.com a través del proxy (S6).
- En modo evaluación el ejecutor no permite apply ni destroy (S1) y plan corre
  con la cuenta Viewer (S5).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sandbox.contract import Diagnostico

PROVEEDORES_PERMITIDOS = {"google", "google-beta"}
FUENTES_PERMITIDAS = {"hashicorp/google", "hashicorp/google-beta"}

MAX_ARCHIVOS = 30
MAX_CARACTERES_ARCHIVO = 256_000

_RE_NOMBRE_ARCHIVO = re.compile(r"^[a-z0-9][a-z0-9_\-]*\.tf$")

# Bloques de primer nivel admitidos. Todo lo demás se rechaza (lista blanca).
_BLOQUES_RAIZ = {"terraform", "provider", "resource", "data", "variable", "output", "locals", "check", "moved"}
_MOTIVOS_RAIZ = {
    "import": "import toma control de recursos que ya existen y destroy los borraría",
    "removed": "removed modifica el estado de recursos existentes",
    "ephemeral": "los recursos ephemeral se ejecutan durante plan",
    "action": "los bloques action invocan acciones del proveedor",
}
_BLOQUES_TERRAFORM = {"required_providers"}
_MOTIVOS_TERRAFORM = {
    "backend": "no se permite declarar backend; el estado es local",
    "cloud": "no se permite el bloque cloud; el estado es local",
}

# Tipos conocidos de otros proveedores, con un motivo más claro que el genérico.
_MOTIVOS_TIPO = {
    "external": "ejecuta programas locales",
    "http": "hace peticiones de red",
    "local_file": "lee o escribe archivos locales",
    "local_sensitive_file": "lee o escribe archivos locales",
    "null_resource": "solo sirve para ejecutar provisioners",
    "terraform_data": "solo sirve para ejecutar provisioners",
    "terraform_remote_state": "lee el estado de otro backend",
}

# Fuentes de datos del proveedor google que exponen o fabrican credenciales.
_DATOS_PROHIBIDOS = {
    "google_client_config": "expone el token de acceso de la cuenta que ejecuta",
    "google_client_openid_userinfo": "expone la identidad de la cuenta que ejecuta",
    "google_service_account_access_token": "genera tokens de otra cuenta de servicio",
    "google_service_account_id_token": "genera tokens de identidad",
    "google_service_account_jwt": "firma tokens con una cuenta de servicio",
    "google_storage_object_signed_url": "firma URLs con credenciales",
}

# Bloques anidados prohibidos en cualquier nivel de un resource o data.
_BLOQUES_ANIDADOS_PROHIBIDOS = {
    "provisioner": "los provisioners ejecutan comandos",
    "connection": "connection abre sesiones SSH o WinRM",
}

# Atributos del bloque provider: la credencial y los endpoints los decide el sandbox.
_ATRIBUTOS_PROVEEDOR_PROHIBIDOS = {
    "credentials": "las credenciales las pone el sandbox",
    "access_token": "las credenciales las pone el sandbox",
    "impersonate_service_account": "la cuenta de servicio la elige el sandbox (Viewer en evaluación)",
    "impersonate_service_account_delegates": "la cuenta de servicio la elige el sandbox",
    "universe_domain": "no se permite cambiar el dominio de las APIs",
}
_BLOQUES_PROVEEDOR_PROHIBIDOS = {"external_credentials": "las credenciales las pone el sandbox"}

# Funciones que leen archivos del sandbox (por ejemplo, la credencial montada).
_FUNCIONES_PROHIBIDAS = (
    "file", "filebase64", "filebase64sha256", "filebase64sha512", "fileexists", "fileset",
    "filemd5", "filesha1", "filesha256", "filesha512", "templatefile", "pathexpand",
)
_RE_FUNCION = re.compile(r"(?<![\w.:\-])(" + "|".join(_FUNCIONES_PROHIBIDAS) + r")\s*\(")
_RE_REF_PROVEEDOR = re.compile(r"^(google|google-beta)(\.[A-Za-z_][A-Za-z0-9_\-]*)?$")
# En un objeto HCL la clave puede ir sin comillas o entre comillas, y separarse con = o con :.
_RE_CLAVE_SOURCE = re.compile(r"(?<![\w\-.])source\s*[=:]")
_RE_SEPARADOR = re.compile(r"\s*[=:]")


# --------------------------------------------------------------------------- análisis léxico

class ErrorAnalisis(ValueError):
    def __init__(self, motivo: str, pos: int) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.pos = pos


@dataclass
class _Cadena:
    inicio: int          # posición de la comilla inicial
    fin: int             # posición de la comilla final
    valor: str | None    # contenido literal; None si tiene interpolaciones


_RE_HEREDOC = re.compile(r"<<-?([A-Za-z_][A-Za-z0-9_\-]*)[ \t]*\r?\n")
_RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*")
_ESPACIOS = " \t\r﻿"


class _Lexico:
    """Produce tres vistas del texto, del mismo largo y con los saltos de línea intactos.

    estructura: sin comentarios y con el contenido de cadenas y heredocs en
        blanco (quedan solo las comillas de las cadenas de primer nivel). Sirve
        para recorrer bloques sin que una llave dentro de una cadena confunda.
    codigo: sin comentarios y sin texto literal, pero conserva el código de las
        interpolaciones ${...}. Sirve para buscar llamadas a funciones.
    sin_comentarios: el texto original sin comentarios, para mostrar valores.
    """

    def __init__(self, texto: str) -> None:
        self.t = texto
        self.n = len(texto)
        self.est = list(texto)
        self.cod = list(texto)
        self.sin_com = list(texto)
        self.cadenas: dict[int, _Cadena] = {}
        self.codigo(0, 0)

    def _borrar(self, a: int, b: int, *, est: bool = True, cod: bool = True, com: bool = False) -> None:
        for k in range(a, b):
            if self.t[k] != "\n":
                if est:
                    self.est[k] = " "
                if cod:
                    self.cod[k] = " "
                if com:
                    self.sin_com[k] = " "

    def codigo(self, i: int, nivel: int) -> int:
        """Recorre código. En una interpolación (nivel > 0) devuelve la posición de la llave que la cierra."""
        t, n = self.t, self.n
        inicio, prof = i, 0
        while i < n:
            c = t[i]
            if c == "#" or t.startswith("//", i):
                fin = t.find("\n", i)
                fin = n if fin == -1 else fin
                self._borrar(i, fin, com=True)
                i = fin
            elif t.startswith("/*", i):
                fin = t.find("*/", i + 2)
                if fin == -1:
                    raise ErrorAnalisis("comentario /* sin cerrar", i)
                self._borrar(i, fin + 2, com=True)
                i = fin + 2
            elif c == '"':
                i = self.cadena(i, nivel) + 1
            elif c == "<" and (m := _RE_HEREDOC.match(t, i)):
                i = self.heredoc(i, m, nivel)
            else:
                if c == "{":
                    prof += 1
                elif c == "}":
                    if prof == 0 and nivel > 0:
                        return i
                    prof -= 1
                i += 1
        if nivel > 0:
            raise ErrorAnalisis("interpolación ${ sin cerrar", inicio)
        return n

    def cadena(self, i: int, nivel: int) -> int:
        """i apunta a la comilla inicial; devuelve la posición de la comilla final."""
        t, n = self.t, self.n
        inicio, j = i, i + 1
        literal, con_interp = j, False
        while True:
            if j >= n or t[j] == "\n":
                raise ErrorAnalisis("cadena sin cerrar", inicio)
            c = t[j]
            if c == "\\":
                if j + 1 >= n or t[j + 1] == "\n":
                    raise ErrorAnalisis("cadena sin cerrar", inicio)
                j += 2
            elif c == '"':
                break
            elif t.startswith("$${", j) or t.startswith("%%{", j):
                j += 3
            elif t.startswith("${", j) or t.startswith("%{", j):
                self._borrar(literal, j)
                con_interp = True
                j = self.codigo(j + 2, nivel + 1) + 1
                literal = j
            else:
                j += 1
        self._borrar(literal, j)
        if nivel == 0:
            self._borrar(inicio + 1, j, cod=False)
            self.cadenas[inicio] = _Cadena(inicio, j, None if con_interp else t[inicio + 1:j])
        else:
            self._borrar(inicio, j + 1, cod=False)
        return j

    def heredoc(self, i: int, m: re.Match, nivel: int) -> int:
        """Devuelve la posición del salto de línea que sigue al marcador final."""
        t, n = self.t, self.n
        marcador = m.group(1)
        j = literal = m.end()
        while True:
            if j == m.end() or t[j - 1] == "\n":
                fin_linea = t.find("\n", j)
                fin_linea = n if fin_linea == -1 else fin_linea
                if t[j:fin_linea].strip() == marcador:
                    self._borrar(literal, j)
                    self._borrar(i, m.end())
                    self._borrar(j, fin_linea)
                    self._borrar(i, fin_linea, cod=False)
                    return fin_linea
            if j >= n:
                raise ErrorAnalisis(f"heredoc <<{marcador} sin cerrar", i)
            if t.startswith("$${", j) or t.startswith("%%{", j):
                j += 3
            elif t.startswith("${", j) or t.startswith("%{", j):
                self._borrar(literal, j)
                j = self.codigo(j + 2, nivel + 1) + 1
                literal = j
            else:
                j += 1


# --------------------------------------------------------------------------- bloques

@dataclass
class _Atributo:
    nombre: str
    pos: int
    ini: int   # inicio del valor
    fin: int   # fin del valor (exclusivo)


@dataclass
class _Bloque:
    tipo: str
    etiquetas: list[str | None]
    pos: int
    atributos: list[_Atributo] = field(default_factory=list)
    bloques: list["_Bloque"] = field(default_factory=list)


class _Bloques:
    """Recorre la vista de estructura y arma el árbol de bloques y atributos."""

    def __init__(self, lex: _Lexico) -> None:
        self.est = "".join(lex.est)
        self.n = len(self.est)
        self.cadenas = lex.cadenas

    def _saltar(self, i: int, saltos: bool = True) -> int:
        est, n = self.est, self.n
        while i < n and (est[i] in _ESPACIOS or (saltos and est[i] == "\n")):
            i += 1
        return i

    def raiz(self) -> _Bloque:
        raiz = _Bloque("", [], 0)
        self._cuerpo(raiz, 0, cerrado=False)
        return raiz

    def _cuerpo(self, bloque: _Bloque, i: int, cerrado: bool) -> int:
        est, n = self.est, self.n
        while True:
            i = self._saltar(i)
            if i >= n:
                if cerrado:
                    raise ErrorAnalisis(f'bloque "{bloque.tipo}" sin cerrar', bloque.pos)
                return i
            if est[i] == "}":
                if not cerrado:
                    raise ErrorAnalisis("llave } de más", i)
                return i
            m = _RE_IDENT.match(est, i)
            if not m:
                raise ErrorAnalisis(f"sintaxis no reconocida cerca de {est[i]!r}", i)
            nombre, j = m.group(), self._saltar(m.end(), saltos=False)
            if j < n and est[j] == "=" and not est.startswith("==", j):
                fin = self._fin_expresion(j + 1)
                bloque.atributos.append(_Atributo(nombre, i, j + 1, fin))
                i = fin
                continue
            etiquetas: list[str | None] = []
            while j < n:
                if est[j] == '"' and j in self.cadenas:
                    cad = self.cadenas[j]
                    etiquetas.append(cad.valor)
                    j = self._saltar(cad.fin + 1, saltos=False)
                elif mi := _RE_IDENT.match(est, j):
                    etiquetas.append(mi.group())
                    j = self._saltar(mi.end(), saltos=False)
                else:
                    break
            if j < n and est[j] == "{":
                hijo = _Bloque(nombre, etiquetas, i)
                i = self._cuerpo(hijo, j + 1, cerrado=True) + 1
                bloque.bloques.append(hijo)
                continue
            raise ErrorAnalisis(f'se esperaba "=" o "{{" después de "{nombre}"', i)

    def _fin_expresion(self, i: int) -> int:
        est, n = self.est, self.n
        inicio, prof = i, 0
        while i < n:
            c = est[i]
            if c in "([{":
                prof += 1
            elif c in ")]}":
                if prof == 0:
                    if c == "}":
                        return i
                    raise ErrorAnalisis(f"{c} sin abrir", i)
                prof -= 1
            elif c == "\n" and prof == 0:
                return i
            i += 1
        if prof:
            raise ErrorAnalisis("expresión sin cerrar", inicio)
        return n


# --------------------------------------------------------------------------- revisión

class _Revision:
    def __init__(self, nombre: str, texto: str, diags: list[Diagnostico]) -> None:
        self.nombre = nombre
        self.texto = texto
        self.diags = diags

    def error(self, mensaje: str, pos: int | None = None) -> None:
        linea = self.texto.count("\n", 0, pos) + 1 if pos is not None else None
        self.diags.append(Diagnostico(control="guard", mensaje=mensaje, archivo=self.nombre, linea=linea))

    def correr(self) -> None:
        try:
            lex = _Lexico(self.texto)
            raiz = _Bloques(lex).raiz()
        except ErrorAnalisis as e:
            self.error(f"No se pudo analizar el archivo: {e.motivo}. Corrige la sintaxis HCL.", e.pos)
            return
        self.est = "".join(lex.est)
        self.cod = "".join(lex.cod)
        self.sin_com = "".join(lex.sin_com)
        self.cadenas = lex.cadenas

        for m in _RE_FUNCION.finditer(self.cod):
            self.error(f"Función no permitida: {m.group(1)}() lee archivos del sandbox.", m.start())

        for b in raiz.bloques:
            if b.tipo not in _BLOQUES_RAIZ:
                self._raiz_no_permitida(b)
            elif b.tipo == "terraform":
                self._terraform(b)
            elif b.tipo == "provider":
                self._proveedor(b)
            elif b.tipo in ("resource", "data"):
                self._recurso(b)
            elif b.tipo == "check":
                for hijo in b.bloques:
                    if hijo.tipo == "data":
                        self._recurso(hijo)

    def _valor(self, a: _Atributo) -> str:
        return self.sin_com[a.ini:a.fin].strip()

    def _posiciones_source(self, a: _Atributo) -> list[int]:
        """Posición del valor de cada clave source dentro del objeto del atributo."""
        posiciones = [m.end() for m in _RE_CLAVE_SOURCE.finditer(self.est, a.ini, a.fin)]
        for ini, cad in self.cadenas.items():
            if a.ini <= ini < a.fin and cad.valor == "source":
                if m := _RE_SEPARADOR.match(self.est, cad.fin + 1):
                    posiciones.append(m.end())
        salida = []
        for p in posiciones:
            while p < a.fin and self.est[p] in _ESPACIOS:
                p += 1
            salida.append(p)
        return salida

    def _etiqueta(self, b: _Bloque) -> str | None:
        """Primera etiqueta del bloque, o None (con error) si falta o no es literal."""
        if not b.etiquetas:
            self.error(f'El bloque {b.tipo} no tiene tipo ni nombre.', b.pos)
            return None
        e = b.etiquetas[0]
        if e is None or "\\" in e:
            self.error(f"La etiqueta del bloque {b.tipo} debe ser un nombre literal, sin interpolaciones "
                       "ni secuencias de escape.", b.pos)
            return None
        return e

    def _raiz_no_permitida(self, b: _Bloque) -> None:
        if b.tipo == "module":
            nombre = b.etiquetas[0] if b.etiquetas and b.etiquetas[0] else "?"
            self.error(f'Módulo "{nombre}" no permitido: el sandbox solo admite los archivos .tf del proyecto, '
                       "sin módulos locales ni remotos.", b.pos)
        elif b.tipo in _MOTIVOS_RAIZ:
            self.error(f"Bloque no permitido: {_MOTIVOS_RAIZ[b.tipo]}.", b.pos)
        else:
            self.error(f'Bloque "{b.tipo}" no permitido en un archivo de Terraform del proyecto.', b.pos)

    def _terraform(self, b: _Bloque) -> None:
        for hijo in b.bloques:
            if hijo.tipo == "required_providers":
                self._required_providers(hijo)
            elif hijo.tipo in _MOTIVOS_TERRAFORM:
                self.error(f"Bloque no permitido: {_MOTIVOS_TERRAFORM[hijo.tipo]}.", hijo.pos)
            else:
                self.error(f'Bloque "{hijo.tipo}" no permitido dentro de terraform.', hijo.pos)

    def _required_providers(self, b: _Bloque) -> None:
        for hijo in b.bloques:
            self.error(f'Sintaxis no permitida en required_providers: "{hijo.tipo}".', hijo.pos)
        for a in b.atributos:
            if a.nombre not in PROVEEDORES_PERMITIDOS:
                self.error(f'Proveedor no permitido en required_providers: "{a.nombre}". '
                           "Usa solo google y google-beta.", a.pos)
            for pos in self._posiciones_source(a):
                cad = self.cadenas.get(pos)
                fuente = cad.valor if cad else None
                if fuente is None or "\\" in fuente:
                    self.error("La fuente del proveedor debe ser una cadena literal.", pos)
                elif _normalizar_fuente(fuente) not in FUENTES_PERMITIDAS:
                    self.error(f'Fuente de proveedor no permitida: "{fuente}". '
                               "Usa hashicorp/google o hashicorp/google-beta.", pos)

    def _proveedor(self, b: _Bloque) -> None:
        nombre = self._etiqueta(b)
        if nombre is None:
            return
        if nombre not in PROVEEDORES_PERMITIDOS:
            self.error(f'Proveedor no permitido: "{nombre}". Usa solo google y google-beta.', b.pos)
        for a in b.atributos:
            if a.nombre in _ATRIBUTOS_PROVEEDOR_PROHIBIDOS:
                self.error(f'Argumento no permitido en provider "{nombre}": {a.nombre}; '
                           f"{_ATRIBUTOS_PROVEEDOR_PROHIBIDOS[a.nombre]}.", a.pos)
            elif a.nombre.endswith("_custom_endpoint"):
                self.error(f'Argumento no permitido en provider "{nombre}": {a.nombre}; '
                           "no se permiten endpoints personalizados.", a.pos)
        for hijo in b.bloques:
            if hijo.tipo in _BLOQUES_PROVEEDOR_PROHIBIDOS:
                self.error(f'Bloque no permitido en provider "{nombre}": {hijo.tipo}; '
                           f"{_BLOQUES_PROVEEDOR_PROHIBIDOS[hijo.tipo]}.", hijo.pos)

    def _recurso(self, b: _Bloque) -> None:
        tipo = self._etiqueta(b)
        if tipo is not None:
            if tipo.split("_", 1)[0] != "google":
                motivo = _MOTIVOS_TIPO.get(tipo)
                if motivo:
                    self.error(f'Bloque no permitido: {b.tipo} "{tipo}" {motivo}.', b.pos)
                self.error(f'Tipo "{tipo}" pertenece a un proveedor no permitido. Usa solo recursos google_*.', b.pos)
            elif b.tipo == "data" and tipo in _DATOS_PROHIBIDOS:
                self.error(f'Fuente de datos no permitida: "{tipo}" {_DATOS_PROHIBIDOS[tipo]}.', b.pos)
        for a in b.atributos:
            if a.nombre == "provider" and not _RE_REF_PROVEEDOR.match(self._valor(a)):
                self.error(f'El {b.tipo} usa un proveedor no permitido: "{self._valor(a)}".', a.pos)
        self._anidados(b)

    def _anidados(self, b: _Bloque) -> None:
        for hijo in b.bloques:
            tipo = hijo.tipo
            if tipo == "dynamic" and hijo.etiquetas:
                tipo = hijo.etiquetas[0] or ""
            if tipo in _BLOQUES_ANIDADOS_PROHIBIDOS:
                self.error(f"Bloque no permitido: {_BLOQUES_ANIDADOS_PROHIBIDOS[tipo]}.", hijo.pos)
            self._anidados(hijo)


def _normalizar_fuente(fuente: str) -> str:
    partes = fuente.strip().lower().split("/")
    if len(partes) == 3 and partes[0] == "registry.terraform.io":
        partes = partes[1:]
    return "/".join(partes)


def revisar(archivos: dict[str, str]) -> list[Diagnostico]:
    diags: list[Diagnostico] = []
    if len(archivos) > MAX_ARCHIVOS:
        diags.append(Diagnostico(control="guard", mensaje=f"Demasiados archivos ({len(archivos)}); "
                                                          f"el máximo es {MAX_ARCHIVOS}."))
        return diags
    for nombre, texto in archivos.items():
        if not _RE_NOMBRE_ARCHIVO.match(nombre):
            diags.append(Diagnostico(control="guard", archivo=nombre,
                                     mensaje=f'Nombre de archivo no permitido: "{nombre}". '
                                             "Solo archivos .tf en la raíz del proyecto."))
            continue
        if len(texto) > MAX_CARACTERES_ARCHIVO:
            diags.append(Diagnostico(control="guard", archivo=nombre,
                                     mensaje=f"Archivo demasiado grande ({len(texto)} caracteres)."))
            continue
        _Revision(nombre, texto, diags).correr()
    return diags
