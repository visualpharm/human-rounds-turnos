"""pii.py — saca los datos que identifican a una persona ANTES de que un
texto salga hacia un proveedor de IA externo.

Por qué existe
--------------
Ninguna de las llamadas de IA de este sistema corre adentro del hospital:
todas salen por `ai_client.call()` a OpenRouter y de ahí al proveedor del
modelo, que puede estar en otro país. Que el contenido clínico salga es
inevitable — es literalmente lo que se le pide al modelo que lea. Que salga
CON NOMBRE Y APELLIDO no lo es (responsable del producto, 2026-08-03: "no mandes información
personal identificable cuando no hace falta — chat, historia clínica —
agarrala antes de mandarla y reemplazá nombre, apellido y esas cosas").

Qué hace y qué no
-----------------
Seudonimización REVERSIBLE, no borrado. Cada valor identificatorio se cambia
por una etiqueta estable dentro del pedido (`[nombre 1]`, `[documento 1]`), el
modelo trabaja con las etiquetas, y la respuesta se pasa por `restore()`
antes de mostrarla. Así el asistente puede seguir diciendo "el turno de
Ana" sin que el proveedor sepa nunca que existe una Ana.

Dos fuentes de detección, en este orden:

1. **Lo que ya sabemos.** La identidad de la sesión (y la de las personas a
   cargo): nombre, apellido, DNI, teléfono, email, domicilio. Es la parte
   confiable — no adivina nada, reemplaza valores exactos.
2. **Barrido genérico.** Expresiones regulares sobre lo que quede: email,
   CUIL/CUIT, teléfono y DNI sueltos que la persona haya tipeado a mano.

DONDE NO SE APLICA, a propósito
-------------------------------
La **imagen o el PDF de la orden de derivación** (`ai_referral.py`,
`ocr_dni.py`) viaja SIN TOCAR. Leer la orden entera es para lo que se usa el
modelo, y taparle partes a una foto haría imposible verificar que la orden es
válida. Está dicho con todas las letras en la política de privacidad
(`legal.py`), que es el lugar donde tiene que estar dicho.

Tampoco se toca el mensaje `system`: son instrucciones nuestras, no contenido
de nadie, y pasarles el barrido genérico sólo podría romperlas.

Límite honesto: el filtro es automático. Un nombre mal tipeado, o adentro de
un texto escaneado con errores de OCR, puede pasar. La política de privacidad
promete "esfuerzo razonable", no garantía, porque eso es lo que esto es.
"""

import re

from app.core.ids import digits as _digitos

# tipo -> etiqueta que ve el modelo. En castellano porque el modelo también
# recibe el contenido en castellano; una etiqueta legible se respeta mejor que
# un token opaco tipo <<PII_3>>.
_ETIQUETAS = {
    "nombre": "nombre",
    "documento": "documento",
    "telefono": "teléfono",
    "email": "email",
    "domicilio": "domicilio",
}

# Un valor más corto que esto no se reemplaza. Con 3 entran los nombres
# cortos reales ("Ana", "Luz", "Eva"), que son justo los que más se escriben
# solos; lo que evita romper "avanzado" no es el largo sino el `\b` y la regla
# de mayúscula inicial que aplica `scrub()` a los nombres de una palabra.
_MIN_LARGO = 3

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# UNA sola pasada numérica, clasificada por cantidad de dígitos, en vez de una
# regex por tipo: en un texto clínico los números se pisan entre sí (un DNI de
# 8 dígitos entra en cualquier patrón de teléfono, un CUIL contiene un DNI) y
# tres regex compitiendo etiquetaban el mismo número distinto según el orden.
_NUM_RE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{5,}\d(?![\w/])")
# Lo que NO es un identificador aunque tenga la pinta.
_FECHA_RE = re.compile(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$")
_MILES_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


def _clasificar(bruto):
    """Qué es este número: 'documento', 'telefono' o nada.

    Una fecha (12-05-2026) y un valor de laboratorio con separador de miles
    (1.250.000) tienen la misma cantidad de dígitos que un DNI, y taparlos
    rompe justo el dato clínico que el modelo tiene que leer."""
    bruto = bruto.strip()
    if _FECHA_RE.match(bruto) or _MILES_RE.match(bruto):
        return None
    n = len(_digitos(bruto))
    if n == 11:
        return "documento"          # CUIL/CUIT
    if n >= 9 or bruto.startswith("+"):
        return "telefono"
    if 7 <= n <= 8:
        return "documento"          # DNI
    return None


class Scrubber:
    """Un pedido = un Scrubber. Los tokens valen sólo adentro de ese pedido:
    no hay un mapa global de personas guardado en ningún lado."""

    def __init__(self):
        self._por_valor = {}    # valor original (casefold) -> token
        self._tipo = {}         # valor original (casefold) -> tipo
        self._por_token = {}    # token -> valor original
        self._n = {}            # tipo -> cuántos van

    # ---------------------------------------------------------------- alta

    def token(self, valor, tipo="nombre"):
        """El token de `valor`, creándolo la primera vez. Un mismo valor
        siempre devuelve el mismo token dentro del pedido."""
        valor = str(valor or "").strip()
        if not valor:
            return ""
        clave = valor.casefold()
        if clave in self._por_valor:
            return self._por_valor[clave]
        self._n[tipo] = self._n.get(tipo, 0) + 1
        token = f"[{_ETIQUETAS.get(tipo, tipo)} {self._n[tipo]}]"
        self._por_valor[clave] = token
        self._tipo[clave] = tipo
        self._por_token[token] = valor
        return token

    def learn(self, valor, tipo="nombre"):
        """Registra un valor conocido para que `scrub()` lo reemplace.
        Ignora lo vacío y lo demasiado corto para reemplazar sin romper
        palabras (salvo documentos/teléfonos, que son numéricos)."""
        valor = str(valor or "").strip()
        if not valor:
            return
        if tipo in ("nombre", "domicilio") and len(valor) < _MIN_LARGO:
            return
        self.token(valor, tipo)

    def learn_person(self, persona):
        """Aprende de un dict de persona (fila de `users`/`dependents`, o el
        usuario de sesión). Tolera claves faltantes y formas raras: esto corre
        en el camino caliente de un chat, no puede tirar una excepción."""
        if not isinstance(persona, dict):
            return
        nombre = str(persona.get("nombre") or "").strip()
        apellido = str(persona.get("apellido") or "").strip()
        # El nombre completo primero: reemplazarlo entero deja "[nombre 1]" y
        # no "[nombre 2] [nombre 3]".
        if nombre and apellido:
            self.learn(f"{nombre} {apellido}", "nombre")
            self.learn(f"{apellido} {nombre}", "nombre")
        self.learn(nombre, "nombre")
        self.learn(apellido, "nombre")
        for clave in ("dni", "documento"):
            dni = _digitos(persona.get(clave))
            if dni:
                self.learn(dni, "documento")
        for clave in ("contacto", "telefono", "celular"):
            tel = str(persona.get(clave) or "").strip()
            if tel and _digitos(tel):
                self.learn(tel, "telefono")
                self.learn(_digitos(tel), "telefono")
        email = str(persona.get("email") or "").strip()
        if "@" in email:
            self.learn(email, "email")
        dom = persona.get("domicilio")
        if isinstance(dom, dict):
            self.learn(dom.get("texto"), "domicilio")
            calle, numero = dom.get("calle"), dom.get("numero")
            if calle and numero:
                self.learn(f"{calle} {numero}", "domicilio")
            self.learn(calle, "domicilio")
        elif isinstance(dom, str):
            self.learn(dom, "domicilio")

    def learn_account(self, usuario):
        """La identidad de la sesión más la de cada persona a su cargo: en el
        chat un titular pregunta por los turnos de su hijo tanto como por los
        propios, y esos nombres viajan en el resultado de las tools."""
        self.learn_person(usuario)
        dni = _digitos((usuario or {}).get("dni")) if isinstance(usuario, dict) else ""
        if not dni:
            return
        try:
            from app.core import db_slots
            for dep in db_slots.list_dependents(dni) or []:
                self.learn_person(dep)
        except Exception:  # noqa: BLE001 — sin base, seguimos con lo que sabemos
            pass

    # ------------------------------------------------------------- limpieza

    def scrub(self, texto, generico=True):
        """`texto` con todo lo identificable cambiado por sus etiquetas.

        `generico=False` corre SÓLO el reemplazo de valores conocidos y saltea
        el barrido por expresiones regulares. Es para el mensaje `system`, que
        además de instrucciones nuestras lleva el resumen de las charlas
        anteriores de esta misma persona (`chat_agent._memory_block`): ahí el
        nombre sí tiene que salir, pero pasarle el barrido numérico a un
        prompt podría tapar un número que ES la instrucción."""
        if not texto:
            return texto
        out = str(texto)
        # Lo conocido primero, del valor más largo al más corto: así
        # "Ana Pérez" gana sobre "Ana".
        for valor in sorted(self._por_valor, key=len, reverse=True):
            token = self._por_valor[valor]
            patron = r"\b" + re.escape(valor) + r"\b"
            if self._tipo.get(valor) == "nombre" and " " not in valor:
                # Un apellido de una sola palabra puede ser una palabra común
                # ("Casas", "Cruz", "Molina"). Se reemplaza sólo cuando
                # aparece como nombre propio — en mayúscula inicial o entero
                # en mayúsculas, que es como lo escriben HSI y el mostrador.
                # Reemplazar "molina" en minúscula rompería el texto sin
                # proteger a nadie.
                out = re.sub(patron, lambda m: token if m.group(0)[:1].isupper()
                             else m.group(0), out, flags=re.IGNORECASE)
            else:
                out = re.sub(patron, token, out, flags=re.IGNORECASE)
        if not generico:
            return out
        # Y después lo que la persona haya tipeado y nosotros no sepamos.
        out = _EMAIL_RE.sub(lambda m: self.token(m.group(0), "email"), out)

        def _num(m):
            crudo = m.group(0)
            tipo = _clasificar(crudo)
            if not tipo:
                return crudo
            # Los espacios de los bordes son del texto, no del número.
            izq = len(crudo) - len(crudo.lstrip())
            der = len(crudo) - len(crudo.rstrip())
            return (crudo[:izq] + self.token(crudo.strip(), tipo)
                    + (crudo[len(crudo) - der:] if der else ""))

        return _NUM_RE.sub(_num, out)

    def scrub_json(self, obj):
        """Lo mismo, recorriendo dicts y listas — el resultado de una tool o
        el blob DATOS de un resumen son JSON, no texto plano."""
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, dict):
            return {k: self.scrub_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.scrub_json(v) for v in obj]
        return obj

    def scrub_messages(self, messages):
        """La lista de mensajes de OpenRouter, respetando el contenido
        multiparte (una entrevista manda [{"type":"text"...},
        {"type":"image_url"...}] — el texto se limpia, la imagen se deja
        pasar) y los `tool_calls` que el modelo ya emitió.

        Al `system` se le corre sólo el reemplazo de valores conocidos, por lo
        que explica `scrub()`."""
        salida = []
        for msg in messages or []:
            if not isinstance(msg, dict):
                salida.append(msg)
                continue
            generico = msg.get("role") != "system"
            nuevo = dict(msg)
            contenido = nuevo.get("content")
            if isinstance(contenido, str):
                nuevo["content"] = self.scrub(contenido, generico)
            elif isinstance(contenido, list):
                partes = []
                for parte in contenido:
                    if isinstance(parte, dict) and isinstance(parte.get("text"), str):
                        parte = dict(parte)
                        parte["text"] = self.scrub(parte["text"], generico)
                    partes.append(parte)
                nuevo["content"] = partes
            llamadas = nuevo.get("tool_calls")
            if isinstance(llamadas, list):
                nuevo["tool_calls"] = [self._scrub_tool_call(tc) for tc in llamadas]
            salida.append(nuevo)
        return salida

    def _scrub_tool_call(self, tc):
        """Los argumentos de una tool que el modelo ya pidió vuelven a
        mandarse en el turno siguiente: si en su momento llevaron un nombre,
        no pueden salir en limpio la segunda vez."""
        if not isinstance(tc, dict):
            return tc
        fn = tc.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("arguments"), str):
            return tc
        nuevo = dict(tc)
        nuevo["function"] = dict(fn, arguments=self.scrub(fn["arguments"]))
        return nuevo

    def restore_tool_call(self, tc):
        """El inverso: antes de EJECUTAR la tool, los argumentos vuelven a
        tener los valores reales — las tools corren adentro, contra la base."""
        if not isinstance(tc, dict):
            return tc
        fn = tc.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("arguments"), str):
            return tc
        nuevo = dict(tc)
        nuevo["function"] = dict(fn, arguments=self.restore(fn["arguments"]))
        return nuevo

    # ------------------------------------------------------------ vuelta

    def restore(self, texto):
        """Devuelve los valores reales donde el modelo dejó etiquetas. Lo que
        se guarda y lo que ve la persona lleva los nombres de verdad; el
        seudónimo existe sólo mientras el texto está afuera."""
        if not texto:
            return texto
        out = str(texto)
        for token, valor in self._por_token.items():
            out = out.replace(token, valor)
        return out

    def restore_json(self, obj):
        if isinstance(obj, str):
            return self.restore(obj)
        if isinstance(obj, dict):
            return {k: self.restore_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.restore_json(v) for v in obj]
        return obj

    def __bool__(self):
        return bool(self._por_valor)


def for_account(usuario):
    """Scrubber sembrado con la identidad de la sesión (chat del sitio y de
    WhatsApp)."""
    s = Scrubber()
    s.learn_account(usuario)
    return s


def for_dni(dni):
    """Scrubber sembrado desde el DNI clínico (resumen de historia clínica y
    entrevista previa: ahí no hay sesión, hay un paciente)."""
    s = Scrubber()
    dni = _digitos(dni)
    if not dni:
        return s
    s.learn(dni, "documento")
    try:
        from app.core import db_accounts
        s.learn_person(db_accounts.get_user(dni) or {})
    except Exception:  # noqa: BLE001 — sin base seguimos con el DNI solo
        pass
    return s
