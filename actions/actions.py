"""
Acciones personalizadas para el chatbot educativo shipibo-konibo.

Arquitectura:
  - El corpus léxico se carga dinámicamente desde actions/corpus/palabras.xlsx
    a través de corpus_loader.py (fuente única de verdad, validada por experto).
  - El cuento interactivo usa las grafías canónicas del corpus validado.
  - La evaluación de respuestas considera variantes ortográficas aceptadas
    por el asesor lingüístico.
"""

from typing import Any, Text, Dict, List
import unicodedata

from rasa_sdk import Action, Tracker, FormValidationAction
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.types import DomainDict
from rasa_sdk.events import SlotSet, EventType, FollowupAction, ActiveLoop

# Asegurar que el directorio de actions/ esté en sys.path
# para que corpus_loader sea importable sin importar cómo Rasa carga el paquete.
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

from corpus_loader import (
    VOCABULARIO,
    DICCIONARIO,
    encontrar_palabra,
    siguiente_palabra,
    traducir as _traducir_corpus,
    corpus_disponible,
)

from db import (
    registrar_intento,
    registrar_fragmento_cuento,
    ultima_posicion,
    ultima_posicion_cuento,
    get_resumen_categorias,
    get_resumen_cuento,
)

from cuentos_loader import (
    fragmento as _cuento_fragmento,
    total_fragmentos as _cuento_total_fragmentos,
    titulo_de as _cuento_titulo,
    cuento_por_id,
    cuentos_disponibles,
    CUENTO_PREDETERMINADO,
)



# El cuento se carga dinámicamente desde actions/corpus/cuentos.xlsx
# vía cuentos_loader. Las funciones auxiliares debajo encapsulan el acceso.

def _cuento_id_activo(tracker) -> str:
    """Cuento activo actual; por defecto, el predeterminado del loader."""
    return tracker.get_slot("cuento_actual") or CUENTO_PREDETERMINADO


def _cuento_id_desde_entidades(tracker) -> str:
    """Intenta obtener el cuento elegido desde entidades del último mensaje."""
    entities = tracker.latest_message.get("entities", []) or []
    for ent in entities:
        if ent.get("entity") == "cuento_actual" and ent.get("value"):
            return str(ent.get("value")).strip()
    return ""


def _get_fragmento(tracker, idx: int):
    """Devuelve el fragmento N del cuento activo, o None si no existe."""
    return _cuento_fragmento(_cuento_id_activo(tracker), idx)


def _get_total(tracker) -> int:
    """Cantidad total de fragmentos del cuento activo."""
    return _cuento_total_fragmentos(_cuento_id_activo(tracker))


# ════════════════════════════════════════════════════════════════════
# UTILIDADES
# ════════════════════════════════════════════════════════════════════

def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, sin puntuación final, sin espacios extra."""
    if not texto:
        return ""
    t = texto.lower().strip()
    for c in ".,!?¿¡;:":
        t = t.replace(c, "")
    t = "".join(
        ch for ch in unicodedata.normalize("NFD", t)
        if unicodedata.category(ch) != "Mn"
    )
    return t.strip()


def evaluar_respuesta(
    usuario: str,
    esperada: str,
    variantes: List[str] = None
) -> str:
    """
    Devuelve 'correcto' | 'parcial' | 'incorrecto'.

    Considera:
    - Coincidencia exacta normalizada con la forma canónica.
    - Coincidencia con cualquiera de las variantes aceptadas por el asesor.
    - Coincidencia parcial (la palabra esperada aparece dentro del input).
    """
    if not usuario or not esperada:
        return "incorrecto"
    u, e = normalizar(usuario), normalizar(esperada)
    if u == e:
        return "correcto"
    # Verificar variantes ortográficas aceptadas
    if variantes:
        for v in variantes:
            if v and normalizar(v) == u:
                return "correcto"
    if e in u.split() or e in u:
        return "parcial"
    return "incorrecto"


# ════════════════════════════════════════════════════════════════════
# MODO DE PRÁCTICA — bidireccionalidad
# ════════════════════════════════════════════════════════════════════
# es_a_shp: se muestra la palabra en español, el alumno produce el shipibo
# shp_a_es: se muestra la palabra en shipibo, el alumno reconoce el español
#
# La elección persiste en el slot `modo_practica` y solo cambia cuando
# el usuario lo pide explícitamente. El progreso en DB es agnóstico al
# modo: cada palabra dominada cuenta igual en cualquier dirección.

MODO_ES_A_SHP = "es_a_shp"
MODO_SHP_A_ES = "shp_a_es"

# Palabras vacías en español que se aceptan envolviendo la respuesta
# en modo shp_a_es: "el agua", "es agua", "la mano izquierda".
# Permiten que el alumno responda con frase corta natural sin penalizarlo.
ARTICULOS_ES = {"el", "la", "los", "las", "un", "una", "unos", "unas",
                "es", "son", "lo", "se", "dice", "significa"}


def _get_modo(tracker) -> str:
    """Devuelve el modo de práctica actual, con fallback a es_a_shp."""
    modo = tracker.get_slot("modo_practica")
    return modo if modo in (MODO_ES_A_SHP, MODO_SHP_A_ES) else MODO_ES_A_SHP


def _formular_pregunta(palabra_info: Dict[str, Any], modo: str) -> str:
    """
    Construye el enunciado de la pregunta según el modo.
    palabra_info debe contener al menos "es" y "shp".
    """
    if modo == MODO_SHP_A_ES:
        return f"¿Qué significa **{palabra_info['shp']}** en español?"
    return f"¿Cómo se dice **{palabra_info['es']}** en shipibo?"


def _pista_segun_modo(palabra_info: Dict[str, Any], modo: str) -> str:
    """
    Devuelve la pista pedagógica adecuada al modo.

    - shp_a_es: usa la pista cultural del corpus (describe el concepto
      en español), que es exactamente lo que el alumno debe producir.
    - es_a_shp: la pista cultural no sirve (el alumno ya tiene el español);
      en su lugar se entrega una pista formal sobre la palabra shipibo:
      inicial y longitud. Es modesta pero honesta y no requiere reescribir
      el corpus de pistas.
    """
    if modo == MODO_SHP_A_ES:
        return palabra_info.get(
            "pista", "Piensa en el contexto de esta palabra."
        )

    shp = palabra_info.get("shp", "")
    if not shp:
        return "Piensa en cómo suena esta palabra en shipibo."
    inicial = shp[0].lower()
    n = len(shp.replace(" ", ""))
    return (
        f"La palabra en shipibo empieza con **{inicial}** "
        f"y tiene {n} letras."
    )


def _esperada_y_variantes(palabra_info: Dict[str, Any], modo: str):
    """
    Devuelve (respuesta_esperada, lista_variantes_aceptadas) según el modo.

    En shp_a_es las "variantes" del corpus son grafías shipibo: no aplican
    para una respuesta en español. Por eso se devuelve lista vacía y el
    matching parcial de evaluar_respuesta cubre frases como "es agua".
    """
    if modo == MODO_SHP_A_ES:
        return palabra_info.get("es", ""), []
    return palabra_info.get("shp", ""), palabra_info.get("variantes", [])


def _normalizar_respuesta_es(texto: str) -> str:
    """
    Limpia respuestas en español: quita artículos y muletillas comunes.
    "el agua" -> "agua", "es la mano" -> "mano".
    """
    if not texto:
        return ""
    tokens = [t for t in normalizar(texto).split() if t not in ARTICULOS_ES]
    return " ".join(tokens) if tokens else normalizar(texto)


def _botones_modo(modo_actual: str) -> List[Dict[str, str]]:
    """Botones para elegir/cambiar de modo, marcando el actual."""
    marca_es_shp = "✓ " if modo_actual == MODO_ES_A_SHP else ""
    marca_shp_es = "✓ " if modo_actual == MODO_SHP_A_ES else ""
    return [
        {
            "title": f"{marca_es_shp}Español → Shipibo (producir)",
            "payload": '/seleccionar_modo{"modo":"es_a_shp"}',
        },
        {
            "title": f"{marca_shp_es}Shipibo → Español (reconocer)",
            "payload": '/seleccionar_modo{"modo":"shp_a_es"}',
        },
    ]


def _extraer_modo_de_mensaje(tracker) -> str:
    """
    Si el último mensaje trae un payload con entidad/metadata `modo`
    (típicamente desde botones), lo devuelve. Si no, devuelve "".
    Rasa parsea /intent{"modo":"x"} como entidad `modo`.
    """
    msg = tracker.latest_message or {}
    for ent in msg.get("entities", []):
        if ent.get("entity") == "modo" and ent.get("value") in (
            MODO_ES_A_SHP, MODO_SHP_A_ES
        ):
            return ent["value"]
    # Fallback heurístico por texto, por si el usuario escribe la frase
    texto = normalizar(msg.get("text", ""))
    if "shipibo a espanol" in texto or "shp a es" in texto:
        return MODO_SHP_A_ES
    if "espanol a shipibo" in texto or "es a shp" in texto:
        return MODO_ES_A_SHP
    return ""




# ════════════════════════════════════════════════════════════════════
# FORM — ACTIVIDAD DE VOCABULARIO (Camino 2)
# ════════════════════════════════════════════════════════════════════

# Durante una actividad, estos intents NO se consideran respuesta:
# son interrupciones que deben atenderse sin evaluar el texto como answer.
INTENTS_INTERRUPCION = {
    "pedir_ayuda",
    "pedir_repeticion",
    "pedir_traduccion",
    "expresar_emocion",
    "negacion",
    "despedida",
    "pausar",
}


class ValidateActividadForm(FormValidationAction):
    """Valida el slot respuesta_actividad capturado por from_text.

    Camino 2:
    - Si el alumno escribe una respuesta corta como "jene" o "yapa",
      se guarda como respuesta aunque el NLU haya predicho otro intent.
    - Si el alumno interrumpe con "ayuda", "estoy confundido", "repítelo",
      no se evalúa como respuesta; se da apoyo y se vuelve a pedir respuesta.
    """

    def name(self) -> Text:
        return "validate_actividad_form"

    def validate_respuesta_actividad(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        intent_data = tracker.latest_message.get("intent") or {}
        intent = intent_data.get("name")
        confianza = intent_data.get("confidence", 0.0)

        texto = (slot_value or "").strip()

        if not texto:
            return {"respuesta_actividad": None}

        # FIX: texto_norm y tokens deben definirse ANTES de cualquier uso.
        # (Antes se usaban en PATRONES_CAMBIO antes de existir -> UnboundLocalError.)
        texto_norm = normalizar(texto)
        tokens = texto_norm.split()

        # Detectar intención de cambiar actividad o categoría:
        # cubre tanto la clasificación correcta como la misclasificación como saludo.
        # NOTA: los patrones van SIN tilde porque texto_norm ya está normalizado
        # (normalizar() elimina tildes); con tilde nunca coincidirían.
        PATRONES_CAMBIO = (
            "quiero aprender", "quiero practicar", "aprender vocabulario",
            "practicar vocabulario", "cambiar categoria", "otra categoria",
            "cambiar de categoria", "categoria", "animales", "colores",
            "naturaleza", "objetos", "cuerpo", "quiero un cuento",
            "iniciar cuento", "cuentame",
        )

        es_cambio_contexto = (
            intent in {"iniciar_cuento", "aprender_vocabulario"} and confianza >= 0.6
        ) or (
            # Captura misclasificaciones cuando el texto claramente pide cambio
            any(p in texto_norm for p in PATRONES_CAMBIO)
            and len(tokens) > 1
        )

        if es_cambio_contexto:
            if intent == "iniciar_cuento" or "cuento" in texto_norm:
                dispatcher.utter_message(
                    text=(
                        "Termina esta palabra o escribe **pausa** "
                        "para salir de la actividad actual. 📖"
                    )
                )
            else:
                dispatcher.utter_message(
                    text=(
                        "Termina esta palabra o escribe **pausa** "
                        "para salir de la actividad actual. 🔄"
                    )
                )
            return {"respuesta_actividad": None}

        # Nota: el intent `seleccionar_modo` dentro del form lo intercepta
        # una regla en rules.yml ("Cambiar modo durante actividad"), que
        # desactiva el loop y delega en action_seleccionar_modo. Esa acción
        # aplica el cambio y reactiva el form vía FollowupAction. Aquí no
        # hace falta lógica adicional.

        ayuda_explicita = {
            "ayuda", "ayudame", "pista", "hint",
            "dame pista", "dame una pista",
            "no se", "no entiendo", "me cuesta"
        }

        repeticion_explicita = {
            "repite", "repitelo", "otra vez",
            "puedes repetir", "vuelve a decirlo"
        }

        pausa_explicita = {
            "pausa", "pausar", "salir",
            "luego sigo", "despues sigo", "me voy"
        }

        despedida_explicita = {
            "chao", "chau", "adios", "hasta luego"
        }

        es_ayuda = texto_norm in ayuda_explicita
        es_repeticion = texto_norm in repeticion_explicita
        es_pausa = texto_norm in pausa_explicita
        es_despedida = texto_norm in despedida_explicita

        # Determinar el flujo activo: vocabulario o cuento
        flujo = tracker.get_slot("flujo_actual") or "vocabulario"

        # Caso ayuda/pista: dar pista real, no mensaje genérico
        if es_ayuda or (
            intent in {"pedir_ayuda", "expresar_emocion"}
            and confianza >= 0.75
            and len(tokens) > 1
        ):
            if flujo == "cuento":
                # Pista para el cuento
                idx = int(tracker.get_slot("fragmento_actual") or 0)
                frag = _get_fragmento(tracker, idx)
                ayuda_msg = (frag or {}).get("ayuda") or "Lee el fragmento con calma."
                preg = (frag or {}).get("pregunta") or "Escribe la palabra en shipibo."
                dispatcher.utter_message(text=f"💡 {ayuda_msg}\n\n{preg}")
                return {"respuesta_actividad": None}

            # Pista para vocabulario (respetando el modo de práctica)
            categoria = tracker.get_slot("categoria_actual") or "naturaleza"
            palabra_es = tracker.get_slot("palabra_actual") or ""
            modo = _get_modo(tracker)
            info = encontrar_palabra(categoria, palabra_es)
            pista = _pista_segun_modo(info, modo)
            pregunta = _formular_pregunta(info, modo)
            dispatcher.utter_message(text=f"💡 Pista: {pista}\n{pregunta}")
            return {"respuesta_actividad": None}

        # Caso repetición clara
        if es_repeticion:
            ultima = tracker.get_slot("ultima_respuesta_bot")
            if ultima:
                dispatcher.utter_message(text=f"Lo repito:\n\n{ultima}")
            else:
                dispatcher.utter_message(text="Repito la pregunta: escribe tu respuesta.")
            return {"respuesta_actividad": None}

        # Caso pausa/despedida clara.
        # Se devuelve un valor centinela para que el form cierre y
        # action_evaluar_respuesta_vocab limpie el flujo.
        if es_pausa or es_despedida:
            return {"respuesta_actividad": "__pausa__"}

        # Si no es una interrupción clara, se considera respuesta,
        # aunque DIET la haya clasificado como saludo, pausar o despedida.
        entidades = [
            e.get("value")
            for e in tracker.latest_message.get("entities", [])
            if e.get("entity") == "palabra_objetivo" and e.get("value")
        ]

        respuesta = entidades[0] if entidades else texto
        return {"respuesta_actividad": respuesta}


# ════════════════════════════════════════════════════════════════════
# ACCIONES — HISTORIA 1: VOCABULARIO
# ════════════════════════════════════════════════════════════════════

class ActionIniciarVocabulario(Action):
    def name(self) -> Text:
        return "action_iniciar_vocabulario"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[EventType]:

        # Verificar que el corpus esté cargado antes de acceder
        if not corpus_disponible():
            dispatcher.utter_message(
                text=(
                    "⚠️ El corpus no está disponible.\n"
                    "Verificá que el archivo *palabras.xlsx* esté en "
                    "la carpeta *actions/corpus/*."
                )
            )
            return []

        # Determinar categoría: la que el usuario mencionó, la siguiente,
        # la ultima usada por el usuario, o la primera disponible como fallback.
        texto = (tracker.latest_message or {}).get("text", "").lower()
        CATEGORIAS_VALIDAS = [c for c in VOCABULARIO.keys() if VOCABULARIO.get(c)]

        # 1) Categoría mencionada explícitamente en el texto
        categoria = None
        for cat in CATEGORIAS_VALIDAS:
            if cat in texto:
                categoria = cat
                break

        # 2) "otra", "diferente", "siguiente", "cambiar" → avanzar a la siguiente
        if categoria is None and any(p in texto for p in ("otra", "diferente", "siguiente", "cambiar", "nuevo")):
            cat_actual = tracker.get_slot("categoria_actual") or CATEGORIAS_VALIDAS[0]
            if cat_actual in CATEGORIAS_VALIDAS:
                idx = CATEGORIAS_VALIDAS.index(cat_actual)
                categoria = CATEGORIAS_VALIDAS[(idx + 1) % len(CATEGORIAS_VALIDAS)]
            else:
                categoria = CATEGORIAS_VALIDAS[0]

        # 3) Ultima posicion registrada en DB para el usuario
        palabra_retomada = None
        categoria_retomada = None
        if categoria is None:
            ultima = ultima_posicion(tracker.sender_id)
            if ultima:
                categoria_db, palabra_db = ultima
                if categoria_db in CATEGORIAS_VALIDAS and encontrar_palabra(categoria_db, palabra_db):
                    categoria = categoria_db
                    categoria_retomada = categoria_db
                    palabra_retomada = palabra_db

        # 4) Fallback: primera categoría disponible
        if categoria is None:
            categoria = CATEGORIAS_VALIDAS[0]

        palabras = VOCABULARIO.get(categoria, [])
        if not palabras:
            dispatcher.utter_message(
                text=f"No encontré palabras en la categoría *{categoria}*. Probemos con otra."
            )
            return []

        palabra = palabras[0]
        if palabra_retomada and categoria == categoria_retomada:
            info_retomada = encontrar_palabra(categoria, palabra_retomada)
            if info_retomada:
                palabra = info_retomada

        # ── Modo de práctica ────────────────────────────────────────────
        # Si nunca eligió modo, se muestra el selector como primer paso
        # y no se activa el form todavía. La elección persiste y la próxima
        # vez salta este paso. Los slots de actividad sí se preparan para
        # que, al elegir modo, ya esté todo listo.
        modo_actual = tracker.get_slot("modo_practica")
        primera_vez = modo_actual not in (MODO_ES_A_SHP, MODO_SHP_A_ES)

        if primera_vez:
            dispatcher.utter_message(
                text=(
                    "Antes de empezar: ¿cómo prefieres practicar?\n\n"
                    "• **Español → Shipibo**: te muestro la palabra en español "
                    "y tú me dices cómo se dice en shipibo (producir).\n"
                    "• **Shipibo → Español**: te muestro la palabra en shipibo "
                    "y tú me dices qué significa (reconocer).\n\n"
                    "Puedes cambiar de modo en cualquier momento."
                ),
                buttons=_botones_modo(MODO_ES_A_SHP),
            )
            return [
                SlotSet("flujo_actual", "vocabulario"),
                SlotSet("categoria_actual", categoria),
                SlotSet("palabra_actual", palabra["es"]),
                SlotSet("fragmento_actual", 0),
                SlotSet("intentos_palabra", 0),
                SlotSet("respuesta_actividad", None),
                # No fijamos ultima_respuesta_bot aquí porque no es una
                # pregunta que se pueda "repetir": es el selector inicial.
            ]

        # Camino normal: el usuario ya tiene un modo elegido
        pregunta = _formular_pregunta(palabra, modo_actual)

        if palabra_retomada and palabra["es"] == palabra_retomada:
            mensaje = (
                f"Retomemos tu avance. 🌿\n"
                f"Categoría: *{categoria}*\n\n"
                f"Te quedaste en esta palabra.\n{pregunta}"
            )
        else:
            mensaje = (
                f"¡Vamos a practicar vocabulario! 🌿\n"
                f"Categoría: *{categoria}*\n\n"
                f"{pregunta}"
            )
        dispatcher.utter_message(text=mensaje, buttons=[
            {"title": "Dame una pista", "payload": "dame una pista"},
            {"title": "Saltar palabra",  "payload": "continuar"},
            {"title": "Cambiar modo", "payload": "/seleccionar_modo"},
        ])
        return [
            SlotSet("flujo_actual", "vocabulario"),
            SlotSet("categoria_actual", categoria),
            SlotSet("palabra_actual", palabra["es"]),
            SlotSet("fragmento_actual", 0),
            SlotSet("intentos_palabra", 0),
            SlotSet("respuesta_actividad", None),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]


class ActionSiguientePalabra(Action):
    def name(self) -> Text:
        return "action_siguiente_palabra"

    def run(self, dispatcher, tracker, domain):
        categoria = tracker.get_slot("categoria_actual") or "naturaleza"
        palabra_actual = tracker.get_slot("palabra_actual") or ""
        nueva = siguiente_palabra(categoria, palabra_actual)
        if not nueva:
            dispatcher.utter_message(text="No tengo más palabras en esta categoría por ahora.")
            return []
        modo = _get_modo(tracker)
        pregunta = _formular_pregunta(nueva, modo)
        mensaje = f"Siguiente palabra:\n\n{pregunta}"
        dispatcher.utter_message(text=mensaje)
        return [
            SlotSet("palabra_actual", nueva["es"]),
            SlotSet("intentos_palabra", 0),
            SlotSet("respuesta_actividad", None),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]


class ActionEvaluarRespuestaVocab(Action):
    def name(self) -> Text:
        return "action_evaluar_respuesta_vocab"

    def run(self, dispatcher, tracker, domain):
        # Camino 2: la respuesta llega desde el slot capturado por from_text.
        # Fallback al último texto para conservar compatibilidad con pruebas antiguas.
        texto = tracker.get_slot("respuesta_actividad")
        if not texto:
            texto = (tracker.latest_message or {}).get("text", "")

        # Flujo básico: si el usuario pausa dentro del form, se cierra
        # la actividad y se limpia el contexto para evitar predicciones cruzadas.
        if texto == "__pausa__":
            mensaje = (
                "Actividad pausada. Para iniciar de nuevo, escribe "
                "**aprender vocabulario** o **quiero un cuento**."
            )
            dispatcher.utter_message(text=mensaje)
            return [
                SlotSet("respuesta_actividad", None),
                SlotSet("flujo_actual", "ninguno"),
                SlotSet("palabra_actual", None),
                SlotSet("categoria_actual", None),
                SlotSet("intentos_palabra", 0),
                SlotSet("ultima_respuesta_bot", mensaje),
            ]

        categoria = tracker.get_slot("categoria_actual") or "naturaleza"
        palabra_es = tracker.get_slot("palabra_actual") or ""
        intentos = int(tracker.get_slot("intentos_palabra") or 0)
        modo = _get_modo(tracker)

        info = encontrar_palabra(categoria, palabra_es)
        esperada, variantes = _esperada_y_variantes(info, modo)

        # En modo shp_a_es aceptamos frases naturales: "el agua", "es agua".
        texto_eval = _normalizar_respuesta_es(texto) if modo == MODO_SHP_A_ES else texto
        resultado = evaluar_respuesta(texto_eval, esperada, variantes)

        # Para los mensajes de feedback necesitamos las dos formas, sin
        # importar el modo: se muestra el par completo es↔shp.
        forma_es = info.get("es", "")
        forma_shp = info.get("shp", "")

        # En la DB siempre guardamos el par canónico es/shp. El campo
        # `resultado` ya captura correcto/parcial/incorrecto; el modo
        # no se almacena (decisión: el dominio cuenta igual en ambas
        # direcciones; agregar columna `modo` queda como deuda v2 si
        # se quiere análisis por dirección).

        eventos_base = [SlotSet("respuesta_actividad", None)]

        if resultado == "correcto":
            registrar_intento(
                tracker.sender_id, categoria, palabra_es, forma_shp, "correcto", intentos + 1
            )
            mensaje = (
                f"¡Excelente! ✅ '**{forma_es}**' en shipibo es '**{forma_shp}**'.\n"
                f"¿Seguimos?"
            )
            dispatcher.utter_message(text=mensaje, buttons=[
                {"title": "Siguiente palabra", "payload": "continuar"},
                {"title": "Cambiar modo", "payload": "/seleccionar_modo"},
                {"title": "Pausar", "payload": "pausa"},
            ])
            return eventos_base + [SlotSet("ultima_respuesta_bot", mensaje)]

        if resultado == "parcial":
            registrar_intento(
                tracker.sender_id, categoria, palabra_es, forma_shp, "parcial", intentos + 1
            )
            mensaje = (
                f"Casi. 🤏 Te acercaste, pero la respuesta exacta es '**{esperada}**'.\n"
                f"¿Seguimos?"
            )
            dispatcher.utter_message(text=mensaje, buttons=[
                {"title": "Siguiente palabra", "payload": "continuar"},
            ])
            return eventos_base + [SlotSet("ultima_respuesta_bot", mensaje)]

        # Incorrecto: en el primer error se da pista y se reactiva el form para
        # capturar el siguiente intento con from_text. En el segundo error,
        # se revela la respuesta y se espera confirmación/continuar.
        intentos += 1
        if intentos < 2:
            pista = _pista_segun_modo(info, modo)
            pregunta = _formular_pregunta(info, modo)
            mensaje = f"No es esa. 💡 Pista: {pista}\n{pregunta}"
            dispatcher.utter_message(text=mensaje)
            return eventos_base + [
                SlotSet("intentos_palabra", intentos),
                SlotSet("ultima_respuesta_bot", mensaje),
                FollowupAction("actividad_form"),
            ]

        registrar_intento(
            tracker.sender_id, categoria, palabra_es, forma_shp, "incorrecto", intentos + 1
        )
        mensaje = (
            f"No te preocupes. La respuesta correcta es '**{esperada}**'. 🌱\n"
            f"({forma_es} ↔ {forma_shp})\n"
            f"¿Pasamos a otra palabra?"
        )
        dispatcher.utter_message(text=mensaje, buttons=[
            {"title": "Siguiente palabra", "payload": "continuar"},
        ])
        return eventos_base + [
            SlotSet("intentos_palabra", intentos),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]

class ActionDarPistaVocab(Action):
    def name(self) -> Text:
        return "action_dar_pista_vocab"

    def run(self, dispatcher, tracker, domain):
        categoria = tracker.get_slot("categoria_actual") or "naturaleza"
        palabra_es = tracker.get_slot("palabra_actual") or ""
        modo = _get_modo(tracker)
        info = encontrar_palabra(categoria, palabra_es)
        pista = _pista_segun_modo(info, modo)
        pregunta = _formular_pregunta(info, modo)
        mensaje = f"💡 Pista: {pista}\n{pregunta}"
        dispatcher.utter_message(text=mensaje)
        return [SlotSet("ultima_respuesta_bot", mensaje)]


class ActionRepetirUltimaPalabra(Action):
    def name(self) -> Text:
        return "action_repetir_ultima_palabra"

    def run(self, dispatcher, tracker, domain):
        ultima = tracker.get_slot("ultima_respuesta_bot")
        if ultima:
            dispatcher.utter_message(text=f"Lo repito:\n\n{ultima}")
        else:
            dispatcher.utter_message(text="No tengo nada que repetir todavía.")
        return []


class ActionContinuarVocabulario(Action):
    def name(self) -> Text:
        return "action_continuar_vocabulario"

    def run(self, dispatcher, tracker, domain):
        palabra_es = tracker.get_slot("palabra_actual")
        categoria = tracker.get_slot("categoria_actual")
        if palabra_es and categoria:
            modo = _get_modo(tracker)
            info = encontrar_palabra(categoria, palabra_es)
            if info:
                pregunta = _formular_pregunta(info, modo)
                mensaje = f"Volvamos a lo nuestro. {pregunta}"
                dispatcher.utter_message(text=mensaje)
                return [SlotSet("ultima_respuesta_bot", mensaje)]
        dispatcher.utter_message(text="Escribe **aprender vocabulario** para iniciar una práctica.")
        return []


# ════════════════════════════════════════════════════════════════════
# ACCIONES — HISTORIA 2: CUENTO
# ════════════════════════════════════════════════════════════════════

class ActionIniciarCuento(Action):
    def name(self) -> Text:
        return "action_iniciar_cuento"

    def run(self, dispatcher, tracker, domain):
        if not cuentos_disponibles():
            dispatcher.utter_message(
                text="⚠️ No hay cuentos cargados. Verifica actions/corpus/cuentos.xlsx"
            )
            return []

        # El frontend puede enviar el cuento elegido en el slot `cuento_actual`
        # (vía payload /iniciar_cuento{"cuento_actual":"motelo_tigre"}).
        # Si el usuario eligió un cuento explícitamente, se respeta esa elección
        # y solo se retoma desde la última posición si el cuento coincide.
        cuento_elegido = tracker.get_slot("cuento_actual") or _cuento_id_desde_entidades(tracker)
        cuento_id = cuento_elegido or CUENTO_PREDETERMINADO
        if not cuento_por_id(cuento_id):
            cuento_id = CUENTO_PREDETERMINADO
        idx = 0

        ultima_cuento = ultima_posicion_cuento(tracker.sender_id)
        if ultima_cuento:
            cuento_db, fragmento_db = ultima_cuento
            if cuento_elegido:
                # Elección explícita: retomar solo si es el mismo cuento.
                if cuento_db == cuento_elegido and cuento_por_id(cuento_db):
                    idx = max(0, int(fragmento_db))
            else:
                # Sin elección explícita: retomar el último cuento abierto.
                if cuento_por_id(cuento_db):
                    cuento_id = cuento_db
                    idx = max(0, int(fragmento_db))

        total = _cuento_total_fragmentos(cuento_id)
        if total and idx >= total:
            idx = 0

        frag = _cuento_fragmento(cuento_id, idx)
        if not frag:
            dispatcher.utter_message(text="⚠️ El cuento no tiene fragmentos.")
            return []

        titulo = _cuento_titulo(cuento_id)
        if ultima_cuento and cuento_id == ultima_cuento[0]:
            mensaje = f"📖 Retomemos tu cuento: **{titulo}** — Parte {idx + 1}\n\n{frag['texto']}"
        else:
            mensaje = f"📖 **{titulo}** — Parte {idx + 1}\n\n{frag['texto']}"
        if frag.get("pregunta"):
            mensaje += f"\n\n{frag['pregunta']}"
            botones = [
                {"title": "Pista",   "payload": "/pedir_ayuda"},
                {"title": "Repetir", "payload": "/pedir_repeticion"},
            ]
        else:
            mensaje += "\n\nEscribe 'continuar' para seguir."
            botones = [{"title": "Continuar", "payload": "/continuar"}]
        dispatcher.utter_message(text=mensaje, buttons=botones)

        eventos = [
            SlotSet("flujo_actual", "cuento"),
            SlotSet("cuento_actual", cuento_id),
            SlotSet("fragmento_actual", float(idx)),
            SlotSet("palabra_actual", None),
            SlotSet("categoria_actual", None),
            SlotSet("intentos_palabra", 0),
            SlotSet("respuesta_actividad", None),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]
        # Si el fragmento tiene pregunta, activar el form para capturar la respuesta
        if frag.get("pregunta"):
            eventos.append(ActiveLoop("actividad_form"))
            eventos.append(SlotSet("requested_slot", "respuesta_actividad"))
        return eventos


class ActionSiguienteFragmento(Action):
    def name(self) -> Text:
        return "action_siguiente_fragmento"

    def run(self, dispatcher, tracker, domain):
        idx_actual = int(tracker.get_slot("fragmento_actual") or 0)
        frag_actual = _get_fragmento(tracker, idx_actual)
        cuento_id = _cuento_id_activo(tracker)

        # Marcar como completado el fragmento actual cuando no tiene pregunta.
        # En fragmentos con pregunta, el registro ocurre en ActionEvaluarRespuestaCuento.
        if frag_actual and not frag_actual.get("pregunta"):
            registrar_fragmento_cuento(
                tracker.sender_id, cuento_id, idx_actual, False
            )

        idx = idx_actual + 1
        total = _get_total(tracker)
        if idx >= total:
            mensaje = (
                "Has terminado el cuento. 🌟 "
                "Si quieres aprender vocabulario, selecciónalo en la barra lateral."
            )
            dispatcher.utter_message(text=mensaje, buttons=[
                {"title": "Ver mi progreso",      "payload": "/ver_mi_progreso"},
            ])
            return [
                SlotSet("flujo_actual", "ninguno"),
                SlotSet("fragmento_actual", 0),
                SlotSet("ultima_respuesta_bot", mensaje),
            ]
        frag = _get_fragmento(tracker, idx)
        mensaje = f"📖 Parte {idx + 1}\n\n{frag['texto']}"
        if frag.get("pregunta"):
            mensaje += f"\n\n{frag['pregunta']}"
            botones = [
                {"title": "Pista",   "payload": "/pedir_ayuda"},
                {"title": "Repetir", "payload": "/pedir_repeticion"},
            ]
        else:
            mensaje += "\n\nEscribe 'continuar' para seguir."
            botones = [{"title": "Continuar", "payload": "/continuar"}]
        dispatcher.utter_message(text=mensaje, buttons=botones)

        eventos = [
            SlotSet("fragmento_actual", float(idx)),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]
        if frag.get("pregunta"):
            eventos.append(ActiveLoop("actividad_form"))
            eventos.append(SlotSet("requested_slot", "respuesta_actividad"))
        return eventos


class ActionEvaluarRespuestaCuento(Action):
    def name(self) -> Text:
        return "action_evaluar_respuesta_cuento"

    def run(self, dispatcher, tracker, domain):
        # El texto viene del slot respuesta_actividad (capturado por el form)
        texto = (
            tracker.get_slot("respuesta_actividad")
            or (tracker.latest_message or {}).get("text", "")
        )
        # Limpiar centinela si vino de pausa
        if texto == "__pausa__":
            dispatcher.utter_message(text="Pausamos el cuento. Cuando quieras seguir, escribe **continuar**.")
            return [SlotSet("respuesta_actividad", None)]

        idx = int(tracker.get_slot("fragmento_actual") or 0)
        frag = _get_fragmento(tracker, idx)
        if not frag:
            dispatcher.utter_message(text="No encontré ese fragmento. Escribe 'continuar'.")
            return [SlotSet("respuesta_actividad", None)]

        esperada = frag.get("respuesta_esperada") or ""
        if not esperada:
            dispatcher.utter_message(text="Sigamos con el cuento. Escribe **continuar**.")
            return [SlotSet("respuesta_actividad", None)]

        cuento_id = _cuento_id_activo(tracker)
        resultado = evaluar_respuesta(texto, esperada)
        registrar_fragmento_cuento(
            tracker.sender_id, cuento_id, idx, resultado == "correcto"
        )
        if resultado == "correcto":
            mensaje = f"¡Muy bien! ✅ '{esperada}' es la palabra correcta."
        elif resultado == "parcial":
            mensaje = f"Te acercaste. 🤏 La respuesta es '{esperada}'."
        else:
            mensaje = f"No exactamente. La palabra era '{esperada}'. 🌱"
        dispatcher.utter_message(text=mensaje, buttons=[
            {"title": "Continuar historia", "payload": "/continuar"},
        ])
        return [
            SlotSet("respuesta_actividad", None),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]


class ActionDarAyudaCuento(Action):
    def name(self) -> Text:
        return "action_dar_ayuda_cuento"

    def run(self, dispatcher, tracker, domain):
        idx = int(tracker.get_slot("fragmento_actual") or 0)
        frag = _get_fragmento(tracker, idx)
        if not frag:
            return []
        ayuda = frag.get("ayuda") or "Lee el fragmento con calma."
        mensaje = f"💡 {ayuda}"
        if frag.get("pregunta"):
            mensaje += f"\n\nVuelvo a preguntarte: {frag['pregunta']}"
        dispatcher.utter_message(text=mensaje)
        return [SlotSet("ultima_respuesta_bot", mensaje)]


class ActionRepetirFragmento(Action):
    def name(self) -> Text:
        return "action_repetir_fragmento"

    def run(self, dispatcher, tracker, domain):
        idx = int(tracker.get_slot("fragmento_actual") or 0)
        frag = _get_fragmento(tracker, idx)
        if not frag:
            return []
        mensaje = f"📖 Repito la parte {idx + 1}:\n\n{frag['texto']}"
        if frag.get("pregunta"):
            mensaje += f"\n\n{frag['pregunta']}"
        dispatcher.utter_message(text=mensaje)
        return [SlotSet("ultima_respuesta_bot", mensaje)]


class ActionContinuarCuento(Action):
    def name(self) -> Text:
        return "action_continuar_cuento"

    def run(self, dispatcher, tracker, domain):
        idx = int(tracker.get_slot("fragmento_actual") or 0)
        frag = _get_fragmento(tracker, idx)
        if frag:
            if frag.get("pregunta"):
                mensaje = f"Volvamos al cuento. {frag['pregunta']}"
            else:
                mensaje = "Volvamos al cuento. Escribe **continuar** para seguir."
            dispatcher.utter_message(text=mensaje)
            return [SlotSet("ultima_respuesta_bot", mensaje)]
        dispatcher.utter_message(text="¿Quieres empezar otro cuento o practicar vocabulario?")
        return []


# ════════════════════════════════════════════════════════════════════
# ACCIONES GLOBALES
# ════════════════════════════════════════════════════════════════════

class ActionTraducir(Action):
    def name(self) -> Text:
        return "action_traducir"

    def run(self, dispatcher, tracker, domain):
        texto = (tracker.latest_message or {}).get("text", "")
        # Extracción simple: busca la última palabra significativa del mensaje
        # TODO: real — reemplazar por NER o módulo dedicado.
        palabras = [w for w in normalizar(texto).split() if len(w) > 2]
        stopwords = {"como", "cómo", "que", "qué", "dice", "significa", "esto",
                     "esta", "shipibo", "espanol", "español", "traduce", "una",
                     "palabra", "digo", "dime", "quiero", "traducir"}
        candidatas = [w for w in palabras if w not in stopwords]
        if not candidatas:
            dispatcher.utter_message(
                text="¿Qué palabra quieres traducir? Dímela directamente, por ejemplo: 'agua' o 'jene'."
            )
            return []
        palabra = candidatas[-1]
        traduccion = _traducir_corpus(palabra)
        if traduccion:
            dispatcher.utter_message(text=f"🔄 '{palabra}' se traduce como '**{traduccion}**'.")
        else:
            dispatcher.utter_message(
                text=f"Por ahora no tengo la traducción de '{palabra}' en mi diccionario base. 🌱"
            )
        return []


class ActionRetomarFlujo(Action):
    """Re-emite una invitación amable a continuar el flujo donde el usuario estaba.

    Lógica simple: mira `flujo_actual` y delega a la acción de continuar
    correspondiente. Si no hay flujo, no hace nada.
    """
    def name(self) -> Text:
        return "action_retomar_flujo"

    def run(self, dispatcher, tracker, domain):
        flujo = tracker.get_slot("flujo_actual")
        if flujo == "vocabulario":
            palabra = tracker.get_slot("palabra_actual")
            if palabra:
                mensaje = f"Volvamos. ¿Cómo se dice **{palabra}** en shipibo?"
                dispatcher.utter_message(text=mensaje)
                return [SlotSet("ultima_respuesta_bot", mensaje)]
        elif flujo == "cuento":
            mensaje = "¿Continuamos con el cuento? Dime 'sí' o 'continuar'."
            dispatcher.utter_message(text=mensaje)
            return [SlotSet("ultima_respuesta_bot", mensaje)]
        return []


class ActionSeleccionarModo(Action):
    """
    Cambia el modo de práctica (es_a_shp / shp_a_es) y vuelve a formular
    la palabra actual en el nuevo modo.

    Se dispara por:
    - Intent `seleccionar_modo` (con o sin entidad `modo` en payload).
    - Botón "Cambiar modo" desde feedback de respuesta o desde la pregunta.

    Si la entidad `modo` viene en el payload (típico de un botón), aplica
    ese modo directamente. Si viene "vacío" (el usuario escribió "cambiar modo"
    sin elegir dirección), muestra el selector con ambas opciones.
    """

    def name(self) -> Text:
        return "action_seleccionar_modo"

    def run(self, dispatcher, tracker, domain):
        modo_pedido = _extraer_modo_de_mensaje(tracker)
        modo_actual = _get_modo(tracker)

        # Caso 1: el usuario ya eligió una dirección concreta (vino por botón
        # o por una frase explícita). Aplicar y reformular la pregunta.
        if modo_pedido in (MODO_ES_A_SHP, MODO_SHP_A_ES):
            etiqueta = (
                "Español → Shipibo (producir)"
                if modo_pedido == MODO_ES_A_SHP
                else "Shipibo → Español (reconocer)"
            )
            confirmacion = f"Listo, modo activo: **{etiqueta}**."

            categoria = tracker.get_slot("categoria_actual")
            palabra_es = tracker.get_slot("palabra_actual")
            flujo = tracker.get_slot("flujo_actual")

            eventos: List[EventType] = [SlotSet("modo_practica", modo_pedido)]

            # Si ya hay una palabra en curso, reformularla en el nuevo modo
            # y reactivar el form para que el alumno responda enseguida.
            if categoria and palabra_es and flujo == "vocabulario":
                info = encontrar_palabra(categoria, palabra_es)
                if info:
                    pregunta = _formular_pregunta(info, modo_pedido)
                    mensaje = f"{confirmacion}\n\n{pregunta}"
                    dispatcher.utter_message(text=mensaje)
                    eventos += [
                        SlotSet("intentos_palabra", 0),
                        SlotSet("respuesta_actividad", None),
                        SlotSet("ultima_respuesta_bot", mensaje),
                        FollowupAction("actividad_form"),
                    ]
                    return eventos

            # Si no hay actividad activa, solo confirmar y sugerir empezar.
            mensaje = (
                f"{confirmacion}\n\n"
                "Escribe **aprender vocabulario** cuando quieras practicar."
            )
            dispatcher.utter_message(text=mensaje)
            eventos.append(SlotSet("ultima_respuesta_bot", mensaje))
            return eventos

        # Caso 2: el usuario pidió "cambiar modo" sin especificar dirección.
        # Mostrar el selector con el modo actual marcado.
        dispatcher.utter_message(
            text="¿Qué modo prefieres? (el actual está marcado con ✓)",
            buttons=_botones_modo(modo_actual),
        )
        return []


class ActionVerProgreso(Action):
    """Reporte de progreso del usuario, leído desde la DB."""

    def name(self) -> Text:
        return "action_ver_progreso"

    def run(self, dispatcher, tracker, domain):
        sid = tracker.sender_id
        cats = get_resumen_categorias(sid)
        cuentos = get_resumen_cuento(sid)

        tiene_datos = any(c["dominadas"] > 0 for c in cats) or len(cuentos) > 0
        if not tiene_datos:
            dispatcher.utter_message(
                text="Aún no tienes progreso registrado. 🌱 ¡Empieza una actividad!",
                buttons=[
                    {"title": "Aprender vocabulario", "payload": "/aprender_vocabulario"},
                    {"title": "Explorar cuento",      "payload": "/iniciar_cuento"},
                ]
            )
            return []

        lineas = ["📊 **Tu progreso:**\n"]
        for c in cats:
            barra = "█" * (c["porcentaje"] // 10) + "░" * (10 - c["porcentaje"] // 10)
            lineas.append(
                f"{c['emoji']} **{c['categoria'].capitalize()}**: "
                f"{c['dominadas']}/{c['total']} ({c['porcentaje']}%)  {barra}"
            )

        if cuentos:
            lineas.append("\n📖 **Cuentos:**")
            for cu in cuentos:
                lineas.append(f"• {cu['cuento_id']}: {cu['completados']} fragmentos")

        total_dom = sum(c["dominadas"] for c in cats)
        total_pal = sum(c["total"] for c in cats)
        pct = round(total_dom / total_pal * 100) if total_pal else 0
        lineas.append(f"\n🏆 **Total: {total_dom}/{total_pal} ({pct}%)**")

        dispatcher.utter_message(
            text="\n".join(lineas),
            buttons=[
                {"title": "Seguir practicando", "payload": "/aprender_vocabulario"},
                {"title": "Ir al cuento",        "payload": "/iniciar_cuento"},
            ]
        )
        return []
