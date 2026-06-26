"""
Acciones personalizadas para el chatbot educativo shipibo-konibo.

Arquitectura:
  - El corpus léxico se carga dinámicamente desde actions/corpus/palabras.xlsx
    a través de corpus_loader.py (fuente única de verdad, validada por experto).
  - El cuento interactivo usa las grafías canónicas del corpus validado.
  - La evaluación de respuestas considera variantes ortográficas aceptadas
    por el asesor lingüístico.
"""

from typing import Any, Text, Dict, List, Optional
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
    ultima_palabra_en_categoria,
    ultimo_fragmento_acertado,
    get_resumen_categorias,
    get_resumen_cuento,
    dominio_global,
)

from cuentos_loader import (
    fragmento as _cuento_fragmento,
    total_fragmentos as _cuento_total_fragmentos,
    titulo_de as _cuento_titulo,
    cuento_por_id,
    cuentos_disponibles,
    CUENTO_PREDETERMINADO,
)

# Import resiliente del loader de frases conversacionales.
try:
    from interaccion_loader import (
        buscar_frase as _buscar_frase_conv,
        frases_ejemplo as _frases_ejemplo,
        frases_disponibles as _frases_conv_disponibles,
        frases_por_categoria as _frases_por_categoria,
    )
except Exception as _int_err:
    import logging as _log_int
    _log_int.getLogger(__name__).warning(
        "interaccion_loader no disponible (%s). "
        "El modo conversación funcionará sin frases conversacionales.", _int_err
    )
    def _buscar_frase_conv(texto):
        return None
    def _frases_ejemplo(n=4):
        return []
    def _frases_conv_disponibles():
        return False
    def _frases_por_categoria(categoria):
        return []

# Import resiliente del loader de curiosidades culturales.
# Si el archivo curiosidades_loader.py no está disponible (por ejemplo,
# no se copió a la carpeta actions/), el sistema sigue funcionando
# sin curiosidades — no rompe vocabulario, cuento ni conversación.
try:
    from curiosidades_loader import (
        obtener_curiosidad as _obtener_curiosidad,
        curiosidades_disponibles as _curiosidades_disponibles,
    )
except Exception as _cur_err:
    import logging as _log_cur
    _log_cur.getLogger(__name__).warning(
        "curiosidades_loader no disponible (%s). "
        "El bot funcionará sin curiosidades culturales.", _cur_err
    )
    def _obtener_curiosidad(palabra_es, probabilidad=0.3, forzar=False):
        return None
    def _curiosidades_disponibles():
        return False

# Import resiliente del cliente HTTP del servicio RAG independiente.
# El servicio RAG vive en otro proyecto (rag-service/) con su propio venv,
# eso permite usar LangChain + pydantic 2 sin chocar con Rasa 3.6 (pydantic 1).
# Si el servicio no está corriendo o no es alcanzable, los stubs devuelven
# None/False y el bot cae al placeholder textual del bloque 5.
try:
    from rag_client import (
        responder_cultural_simple as _rag_responder,
        rag_disponible as _rag_disponible,
    )
except Exception as _rag_err:
    import logging as _log_rag
    _log_rag.getLogger(__name__).warning(
        "rag_client no disponible (%s). El bot funcionará sin RAG cultural.",
        _rag_err
    )
    def _rag_responder(query):
        return None
    def _rag_disponible():
        return False

import random as _random



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
    return _pista_tecnica_palabra(shp) or "Piensa en cómo suena esta palabra en shipibo."


def _pista_tecnica_palabra(palabra: Optional[str]) -> str:
    """
    Genera una pista técnica reusable basada en la forma de la palabra:
    inicial y cantidad de letras (sin contar espacios).
    Útil para cuentos (donde la palabra esperada está en shipibo) y para
    el modo es→shp de vocabulario.

    Devuelve cadena vacía si la palabra no es válida, para que el llamador
    pueda concatenar condicionalmente sin chequeos extras.
    """
    if not palabra or not isinstance(palabra, str):
        return ""
    p = palabra.strip()
    if not p:
        return ""
    inicial = p[0].upper()
    n_letras = len(p.replace(" ", ""))
    return f"Empieza con **{inicial}** y tiene **{n_letras}** letras."


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
                        "Termina esta palabra "
                        "para salir de la actividad actual. 📖"
                    )
                )
            else:
                dispatcher.utter_message(
                    text=(
                        "Termina esta palabra "
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
            "ayuda", "ayúdame", "pista", "hint",
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
                # Pista para el cuento: cultural + técnica (primera letra y
                # cantidad de letras) si hay respuesta_esperada disponible.
                idx = int(tracker.get_slot("fragmento_actual") or 0)
                frag = _get_fragmento(tracker, idx)
                ayuda_msg = (frag or {}).get("ayuda") or "Lee el fragmento con calma."
                preg = (frag or {}).get("pregunta") or "Escribe la palabra en shipibo."
                pista_tecnica = _pista_tecnica_palabra((frag or {}).get("respuesta_esperada"))
                if pista_tecnica:
                    ayuda_msg = f"{ayuda_msg} {pista_tecnica}"
                dispatcher.utter_message(text=f"💡 {ayuda_msg}\n\n{preg}")
                # Marcar pista usada para tracking pedagógico (Hito 4).
                return {"respuesta_actividad": None, "pista_solicitada": True}

            # Pista para vocabulario (respetando el modo de práctica)
            categoria = tracker.get_slot("categoria_actual") or "naturaleza"
            palabra_es = tracker.get_slot("palabra_actual") or ""
            modo = _get_modo(tracker)
            info = encontrar_palabra(categoria, palabra_es)
            pista = _pista_segun_modo(info, modo)
            pregunta = _formular_pregunta(info, modo)
            dispatcher.utter_message(text=f"💡 Pista: {pista}\n{pregunta}")
            # Marcar pista usada para tracking pedagógico (Hito 4).
            return {"respuesta_actividad": None, "pista_solicitada": True}

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

        # Determinar categoría: por payload (frontend), por texto, retomada
        # de la DB, o fallback a la primera disponible.
        texto = (tracker.latest_message or {}).get("text", "").lower()
        CATEGORIAS_VALIDAS = [c for c in VOCABULARIO.keys() if VOCABULARIO.get(c)]

        # 0) Categoría enviada explícitamente por el frontend en el payload
        #    /aprender_vocabulario{"categoria_actual":"animales"}.
        #    Es la fuente más confiable: el usuario hizo clic en un botón.
        categoria = None
        slot_cat = tracker.get_slot("categoria_actual")
        if slot_cat in CATEGORIAS_VALIDAS:
            # Solo confiar en el slot si vino del último mensaje (entidad o
            # payload). Si era un valor remanente de un turno anterior, lo
            # ignoramos para evitar pegarse a una categoría vieja.
            ents = (tracker.latest_message or {}).get("entities", [])
            from_payload = any(
                e.get("entity") == "categoria_actual" or e.get("value") == slot_cat
                for e in ents
            ) or f'"categoria_actual"' in (tracker.latest_message or {}).get("text", "")
            if from_payload:
                categoria = slot_cat

        # 1) Categoría mencionada explícitamente en el texto
        if categoria is None:
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

        # 3) Si aún no hay categoría (no vino del frontend ni del texto),
        #    retomar la última categoría que el usuario practicó.
        if categoria is None:
            ultima = ultima_posicion(tracker.sender_id)
            if ultima:
                categoria_db, _ = ultima
                if categoria_db in CATEGORIAS_VALIDAS:
                    categoria = categoria_db

        # 4) Fallback: primera categoría disponible
        if categoria is None:
            categoria = CATEGORIAS_VALIDAS[0]

        palabras = VOCABULARIO.get(categoria, [])
        if not palabras:
            dispatcher.utter_message(
                text=f"No encontré palabras en la categoría *{categoria}*. Probemos con otra."
            )
            return []

        # ── Decidir QUÉ palabra mostrar ─────────────────────────────────────
        # Regla: si el usuario ya practicó en esta categoría antes, retomamos
        # en la palabra SIGUIENTE a la última que vio (acertada o no).
        # Si nunca practicó esta categoría, arrancamos en la primera.
        # Esto se aplica siempre, sin importar si la categoría vino del
        # frontend, del texto del usuario o de la BD.
        ultima_palabra_es = ultima_palabra_en_categoria(tracker.sender_id, categoria)
        es_retomada = False
        if ultima_palabra_es:
            info_ultima = encontrar_palabra(categoria, ultima_palabra_es)
            if info_ultima:
                # Avanzar a la siguiente palabra usando el helper del corpus
                palabra = siguiente_palabra(categoria, ultima_palabra_es) or palabras[0]
                es_retomada = True
            else:
                # La palabra ya no existe en el corpus (corpus cambió) → empezar de cero
                palabra = palabras[0]
        else:
            palabra = palabras[0]

        # ── Modo de práctica ────────────────────────────────────────────
        # Si no hay modo elegido todavía, se usa es_a_shp por defecto.
        # El usuario puede cambiarlo después con el botón "Cambiar modo".
        modo_actual = tracker.get_slot("modo_practica")
        if modo_actual not in (MODO_ES_A_SHP, MODO_SHP_A_ES):
            modo_actual = MODO_ES_A_SHP

        # Formular la pregunta directamente, sin selector intermedio
        pregunta = _formular_pregunta(palabra, modo_actual)

        if es_retomada:
            mensaje = (
                f"Retomemos tu avance. 🌿\n"
                f"Categoría: *{categoria}*\n\n"
                f"Continuamos con la siguiente palabra.\n{pregunta}"
            )
        else:
            mensaje = (
                f"¡Vamos a practicar vocabulario! 🌿\n"
                f"Categoría: *{categoria}*\n\n"
                f"{pregunta}"
            )
        dispatcher.utter_message(text=mensaje, buttons=[
            {"title": "Dame una pista", "payload": "dame una pista"},
            {"title": "Saltar palabra",  "payload": "/continuar"},
            {"title": "Cambiar modo", "payload": "/seleccionar_modo"},
        ])
        return [
            SlotSet("flujo_actual", "vocabulario"),
            SlotSet("categoria_actual", categoria),
            SlotSet("palabra_actual", palabra["es"]),
            SlotSet("fragmento_actual", 0),
            SlotSet("intentos_palabra", 0),
            SlotSet("pista_solicitada", False),
            SlotSet("respuesta_actividad", None),
            SlotSet("ultima_respuesta_bot", mensaje),
            SlotSet("modo_practica", modo_actual),
            # Activamos el form desde aquí porque la regla ya no lo hace
            # automáticamente (ver rules.yml).
            FollowupAction("actividad_form"),
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
            SlotSet("pista_solicitada", False),
            SlotSet("respuesta_actividad", None),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]


# Capa 1: número máximo de intentos antes de marcar la palabra como
# "no logra" (nivel 0). Definido en colaboración con la experta pedagógica.
MAX_INTENTOS_VOCAB = 3


class ActionEvaluarRespuestaVocab(Action):
    """
    Evalúa la respuesta del usuario aplicando la curva de aprendizaje de la
    matriz pedagógica validada por la experta en educación:

      • Nivel 2: logra al primer intento.
      • Nivel 1: logra después de 2 o 3 intentos.
      • Nivel 0: no logra (agota los 3 intentos).

    Respuesta parcial (similitud alta pero no exacta) NO cuenta como logro:
    se le pide al usuario corregir y se consume un intento.
    """

    def name(self) -> Text:
        return "action_evaluar_respuesta_vocab"

    def run(self, dispatcher, tracker, domain):
        texto = tracker.get_slot("respuesta_actividad")
        if not texto:
            texto = (tracker.latest_message or {}).get("text", "")

        # Pausar limpia el contexto (igual que antes)
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
                SlotSet("pista_solicitada", False),
                SlotSet("ultima_respuesta_bot", mensaje),
            ]

        categoria = tracker.get_slot("categoria_actual") or "naturaleza"
        palabra_es = tracker.get_slot("palabra_actual") or ""
        intentos_previos = int(tracker.get_slot("intentos_palabra") or 0)
        intento_actual = intentos_previos + 1
        modo = _get_modo(tracker)
        # Lectura del flag de pista usado durante este intento.
        # Se setea en True por ActionDarPistaVocab y se consume aquí.
        pista_usada = bool(tracker.get_slot("pista_solicitada"))

        info = encontrar_palabra(categoria, palabra_es)
        esperada, variantes = _esperada_y_variantes(info, modo)
        forma_es = info.get("es", "")
        forma_shp = info.get("shp", "")

        texto_eval = _normalizar_respuesta_es(texto) if modo == MODO_SHP_A_ES else texto
        resultado = evaluar_respuesta(texto_eval, esperada, variantes)

        eventos_base = [SlotSet("respuesta_actividad", None)]

        # ── RESPUESTA CORRECTA: registrar nivel según número de intentos ────
        if resultado == "correcto":
            if intento_actual == 1:
                nivel = 2  # Nivel alto: lo logra a la primera
                mensaje = (
                    f"¡Excelente! 🎉 Lo lograste al primer intento.\n"
                    f"'**{forma_es}**' en shipibo es '**{forma_shp}**'."
                )
            else:
                nivel = 1  # Nivel medio: lo logra con esfuerzo
                mensaje = (
                    f"¡Muy bien! 👏 Lo lograste con esfuerzo "
                    f"(intento {intento_actual} de {MAX_INTENTOS_VOCAB}).\n"
                    f"'**{forma_es}**' en shipibo es '**{forma_shp}**'."
                )

            registrar_intento(
                tracker.sender_id, categoria, palabra_es, forma_shp,
                "correcto", intento_actual,
                nivel=nivel, criterios="uso,ortografia",
                modo=modo, uso_pista=pista_usada,
            )

            dispatcher.utter_message(text=mensaje + "\n¿Seguimos?", buttons=[
                {"title": "Siguiente palabra", "payload": "/continuar"},
                {"title": "Cambiar modo",      "payload": "/seleccionar_modo"},
            ])
            return eventos_base + [
                SlotSet("intentos_palabra", 0),  # reset para la siguiente palabra
                SlotSet("pista_solicitada", False),  # reset del flag de pista
                SlotSet("ultima_respuesta_bot", mensaje),
            ]

        # ── NO CORRECTA (parcial o incorrecta): aplicar ciclo de reintentos ──
        if intento_actual < MAX_INTENTOS_VOCAB:
            # Aún quedan intentos. Damos feedback y volvemos a pedir la respuesta.
            if resultado == "parcial":
                feedback = "Casi. 🤏 Fíjate en la ortografía y prueba de nuevo."
            else:
                pista = _pista_segun_modo(info, modo)
                feedback = f"No es esa. 💡 Pista: {pista}"

            pregunta = _formular_pregunta(info, modo)
            intentos_restantes = MAX_INTENTOS_VOCAB - intento_actual
            mensaje = (
                f"{feedback}\n\n"
                f"_(Te quedan {intentos_restantes} "
                f"{'intento' if intentos_restantes == 1 else 'intentos'})_\n\n"
                f"{pregunta}"
            )
            dispatcher.utter_message(text=mensaje)
            return eventos_base + [
                SlotSet("intentos_palabra", intento_actual),
                SlotSet("ultima_respuesta_bot", mensaje),
                FollowupAction("actividad_form"),
            ]

        # ── AGOTÓ INTENTOS: revelar respuesta y registrar nivel 0 ───────────
        registrar_intento(
            tracker.sender_id, categoria, palabra_es, forma_shp,
            "incorrecto", intento_actual,
            nivel=0, criterios="uso,ortografia",
            modo=modo, uso_pista=pista_usada,
        )
        mensaje = (
            f"La respuesta era '**{esperada}**'. 🌱\n"
            f"({forma_es} ↔ {forma_shp})\n\n"
            f"No te preocupes, así se aprende. ¿Pasamos a otra palabra?"
        )
        dispatcher.utter_message(text=mensaje, buttons=[
            {"title": "Siguiente palabra", "payload": "/continuar"},
        ])
        return eventos_base + [
            SlotSet("intentos_palabra", 0),  # reset
            SlotSet("pista_solicitada", False),  # reset del flag de pista
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
        # Marcar que se usó pista en este intento. La penalización
        # se aplica en calcular_score_palabra() al evaluar la respuesta.
        return [
            SlotSet("pista_solicitada", True),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]


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

        # ── Determinar el cuento_id a abrir ─────────────────────────────────
        # Si el usuario eligió un cuento explícitamente, se respeta esa elección.
        # Si no, se retoma el último cuento que el usuario abrió.
        ultima_cuento = ultima_posicion_cuento(tracker.sender_id)
        if not cuento_elegido and ultima_cuento and cuento_por_id(ultima_cuento[0]):
            cuento_id = ultima_cuento[0]

        # ── Decidir qué fragmento mostrar (retomar correctamente) ──────────
        # Regla: si el usuario YA acertó algún fragmento de este cuento,
        # arrancamos en el SIGUIENTE al último acertado (no en el último visto,
        # que pudo haber sido fallido y nos llevaría a repetirlo).
        # Si nunca acertó nada, arrancamos en el fragmento 0.
        idx = 0
        resumen_anterior = None
        ultimo_ok = ultimo_fragmento_acertado(tracker.sender_id, cuento_id)
        if ultimo_ok is not None:
            # Guardamos un resumen breve del último fragmento acertado
            # para dar contexto al usuario antes de seguir.
            frag_anterior = _cuento_fragmento(cuento_id, ultimo_ok)
            if frag_anterior:
                texto_ant = (frag_anterior.get("texto") or "").strip()
                # Resumen = primera oración (hasta el primer punto) o primeros 140 chars
                primer_punto = texto_ant.find(". ")
                if 0 < primer_punto < 200:
                    resumen_anterior = texto_ant[:primer_punto + 1]
                else:
                    resumen_anterior = (texto_ant[:140] + "…") if len(texto_ant) > 140 else texto_ant
            idx = ultimo_ok + 1

        total = _cuento_total_fragmentos(cuento_id)
        if total and idx >= total:
            # El usuario terminó el cuento. Reiniciamos en 0 con mensaje.
            idx = 0
            resumen_anterior = None

        frag = _cuento_fragmento(cuento_id, idx)
        if not frag:
            dispatcher.utter_message(text="⚠️ El cuento no tiene fragmentos.")
            return []

        titulo = _cuento_titulo(cuento_id)
        # ── Componer el mensaje ─────────────────────────────────────────────
        # Caso 1: retomamos un cuento con resumen del último acertado
        # Caso 2: retomamos el mismo cuento sin progreso previo acertado
        # Caso 3: arranque limpio (nuevo cuento o reinicio tras completar)
        if resumen_anterior:
            mensaje = (
                f"📖 Retomemos tu cuento: **{titulo}**\n\n"
                f"_Última parte que completaste:_\n"
                f"_\"{resumen_anterior}\"_\n\n"
                f"**Parte {idx + 1}**\n\n{frag['texto']}"
            )
        elif ultima_cuento and cuento_id == ultima_cuento[0]:
            mensaje = (
                f"📖 Retomemos tu cuento: **{titulo}** — Parte {idx + 1}\n\n"
                f"{frag['texto']}"
            )
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
            SlotSet("pista_solicitada", False),
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
                "Has terminado el cuento. 🌟 ¡Buen trabajo!"
            )
            dispatcher.utter_message(text=mensaje)
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
            SlotSet("intentos_palabra", 0),  # reset al avanzar de fragmento
            SlotSet("pista_solicitada", False),  # reset del flag de pista
            SlotSet("ultima_respuesta_bot", mensaje),
        ]
        if frag.get("pregunta"):
            eventos.append(ActiveLoop("actividad_form"))
            eventos.append(SlotSet("requested_slot", "respuesta_actividad"))
        return eventos


# Capa 1: número máximo de intentos en el cuento (misma rúbrica que vocab)
MAX_INTENTOS_CUENTO = 3


class ActionEvaluarRespuestaCuento(Action):
    """
    Evalúa la respuesta del usuario a una pregunta del cuento aplicando
    la curva de aprendizaje pedagógica:

      • Nivel 2: comprende y responde correctamente al primer intento.
      • Nivel 1: comprende y responde correctamente después de varios intentos.
      • Nivel 0: no comprende ni responde correctamente (agota los intentos).

    Respuesta parcial NO cuenta como logro; se solicita corregir y se
    consume un intento. El contador de intentos se reusa el mismo slot
    intentos_palabra (en cuento representa intentos por fragmento).
    """

    def name(self) -> Text:
        return "action_evaluar_respuesta_cuento"

    def run(self, dispatcher, tracker, domain):
        texto = (
            tracker.get_slot("respuesta_actividad")
            or (tracker.latest_message or {}).get("text", "")
        )

        if texto == "__pausa__":
            dispatcher.utter_message(
                text="Pausamos el cuento. Cuando quieras seguir, escribe **continuar**."
            )
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
        intentos_previos = int(tracker.get_slot("intentos_palabra") or 0)
        intento_actual = intentos_previos + 1
        # Flag de pista. Se setea en True por ActionDarAyudaCuento y se
        # consume aquí para registrar uso_pista en ProgresoCuento.
        pista_usada = bool(tracker.get_slot("pista_solicitada"))
        resultado = evaluar_respuesta(texto, esperada)
        eventos_base = [SlotSet("respuesta_actividad", None)]

        # ── RESPUESTA CORRECTA: registrar nivel según intento ───────────────
        if resultado == "correcto":
            if intento_actual == 1:
                nivel = 2
                mensaje = (
                    f"¡Muy bien! 🎉 Lo entendiste al primer intento. "
                    f"'**{esperada}**' es la palabra correcta."
                )
            else:
                nivel = 1
                mensaje = (
                    f"¡Bien hecho! 👏 Lo lograste con esfuerzo "
                    f"(intento {intento_actual} de {MAX_INTENTOS_CUENTO}). "
                    f"'**{esperada}**' es la palabra correcta."
                )

            registrar_fragmento_cuento(
                tracker.sender_id, cuento_id, idx, True,
                nivel=nivel, criterios="uso,comprension",
                uso_pista=pista_usada,
            )
            dispatcher.utter_message(text=mensaje, buttons=[
                {"title": "Continuar historia", "payload": "/continuar"},
            ])
            return eventos_base + [
                SlotSet("intentos_palabra", 0),  # reset para próximo fragmento
                SlotSet("pista_solicitada", False),  # reset del flag de pista
                SlotSet("ultima_respuesta_bot", mensaje),
            ]

        # ── NO CORRECTA: aplicar ciclo de reintentos dentro del cuento ──────
        if intento_actual < MAX_INTENTOS_CUENTO:
            if resultado == "parcial":
                feedback = "Te acercaste. 🤏 Fíjate en la ortografía y prueba de nuevo."
            else:
                ayuda_msg = frag.get("ayuda") or "Lee el fragmento con calma y piensa en el contexto."
                feedback = f"No es esa. 💡 Pista: {ayuda_msg}"

            pregunta = frag.get("pregunta") or "Escribe tu respuesta."
            intentos_restantes = MAX_INTENTOS_CUENTO - intento_actual
            mensaje = (
                f"{feedback}\n\n"
                f"_(Te quedan {intentos_restantes} "
                f"{'intento' if intentos_restantes == 1 else 'intentos'})_\n\n"
                f"{pregunta}"
            )
            dispatcher.utter_message(text=mensaje)
            return eventos_base + [
                SlotSet("intentos_palabra", intento_actual),
                SlotSet("ultima_respuesta_bot", mensaje),
                FollowupAction("actividad_form"),
            ]

        # ── AGOTÓ INTENTOS: revelar y registrar nivel 0 ─────────────────────
        registrar_fragmento_cuento(
            tracker.sender_id, cuento_id, idx, False,
            nivel=0, criterios="uso,comprension",
            uso_pista=pista_usada,
        )
        mensaje = (
            f"La palabra que buscábamos era '**{esperada}**'. 🌱\n\n"
            f"No te preocupes, sigamos con la historia para que aprendas más."
        )
        dispatcher.utter_message(text=mensaje, buttons=[
            {"title": "Continuar historia", "payload": "/continuar"},
        ])
        return eventos_base + [
            SlotSet("intentos_palabra", 0),  # reset
            SlotSet("pista_solicitada", False),  # reset del flag de pista
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
        # Pista cultural del Excel (col "ayuda") + pista técnica generada a
        # partir de respuesta_esperada (inicial + cantidad de letras).
        ayuda = frag.get("ayuda") or "Lee el fragmento con calma."
        pista_tecnica = _pista_tecnica_palabra(frag.get("respuesta_esperada"))
        if pista_tecnica:
            ayuda = f"{ayuda} {pista_tecnica}"
        mensaje = f"💡 {ayuda}"
        if frag.get("pregunta"):
            mensaje += f"\n\nVuelvo a preguntarte: {frag['pregunta']}"
        dispatcher.utter_message(text=mensaje)
        # Marcar que se usó pista. ActionEvaluarRespuestaCuento la pasa a
        # registrar_fragmento_cuento(uso_pista=True) para tracking.
        return [
            SlotSet("pista_solicitada", True),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]


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
                        SlotSet("pista_solicitada", False),
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
    """Reporte de progreso del usuario, leído desde la DB.

    Incluye, además del resumen tradicional por categoría:
    - Score promedio global (sistema ponderado producción ×2 / receptivo ×1)
    - Tasas de acierto por modo de práctica
    - Tasa de uso de pistas (proxy de dificultad percibida)
    """

    def name(self) -> Text:
        return "action_ver_progreso"

    def run(self, dispatcher, tracker, domain):
        sid = tracker.sender_id
        cats = get_resumen_categorias(sid)
        cuentos = get_resumen_cuento(sid)

        # Métricas globales ponderadas (Hito 4). Si DB falla, devuelve ceros
        # y el bloque ponderado simplemente no se muestra.
        try:
            metricas = dominio_global(sid)
        except Exception:
            metricas = {}

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

        # Bloque de scoring ponderado: solo si hay datos significativos
        n_intentos = int(metricas.get("total_intentos", 0))
        if metricas and n_intentos > 0:
            score_prom = metricas.get("score_promedio", 0)
            tasa_prod  = metricas.get("tasa_acierto_es_shp", 0)
            tasa_rec   = metricas.get("tasa_acierto_shp_es", 0)
            tasa_pista = metricas.get("tasa_uso_pista", 0)
            dom        = metricas.get("palabras_dominadas", 0)
            apr        = metricas.get("palabras_aprendiendo", 0)
            nue        = metricas.get("palabras_nuevas", 0)

            lineas.append("\n⭐ **Score ponderado**")
            lineas.append(
                f"• Promedio: **{score_prom}/100**  "
                f"(✅ {dom} dominadas · 📘 {apr} aprendiendo · 🌱 {nue} nuevas)"
            )
            partes_tasa: List[str] = []
            if tasa_prod > 0:
                partes_tasa.append(f"Producir {tasa_prod}%")
            if tasa_rec > 0:
                partes_tasa.append(f"Reconocer {tasa_rec}%")
            if tasa_pista > 0:
                partes_tasa.append(f"Pistas {tasa_pista}%")
            if partes_tasa:
                lineas.append("• " + " · ".join(partes_tasa))

        dispatcher.utter_message(
            text="\n".join(lineas),
            buttons=[
                {"title": "Seguir practicando", "payload": "/aprender_vocabulario"},
                {"title": "Ir al cuento",        "payload": "/iniciar_cuento"},
            ]
        )
        return []


# ════════════════════════════════════════════════════════════════════
# MÓDULO "CONVERSA CONMIGO" — versión naturalizada
# ════════════════════════════════════════════════════════════════════
# Mejoras frente a la versión inicial:
#   • Plantillas múltiples por tipo de respuesta (selección aleatoria)
#   • Quick replies dinámicos contextuales en cada turno
#   • Memoria de última intención (slot ultima_intencion_conv) para
#     evitar repetir el mismo formato cuando el usuario reitera el tema
#   • Detección de despedidas (es y shp) para cerrar con respeto
#   • Curiosidades culturales del PDF (~30% de las traducciones)
#     compatibles con futura sustitución por RAG sobre chunks
#   • Identidad explícita del bot: "Pishico"
# ════════════════════════════════════════════════════════════════════

# Palabras que indican despedida en español o shipibo
_PALABRAS_DESPEDIDA = {
    "chau", "chao", "adios", "adiós", "bye", "hasta luego", "hasta pronto",
    "nos vemos", "me voy", "salir", "salgo",
    "kabanon", "eara jopariai", "jopariai",
}

# Plantillas para FRASES CONVERSACIONALES del loader interaccion_loader
# (saludos, agradecer, cortesía, ayuda). Diseño: todas comparten estructura
# y emoji estable. Solo varía la formulación natural en español. Esto evita
# la sensación de que el bot "cambia de formato" entre turnos.
_TPL_FRASE_SHP = [
    "💬 *{shp}* en español significa *{es}*.",
    "💬 En español, *{shp}* es *{es}*.",
    "💬 *{shp}* → *{es}*.",
]

_TPL_FRASE_ES = [
    "💬 *{es}* en shipibo se dice *{shp}*.",
    "💬 En shipibo, *{es}* es *{shp}*.",
    "💬 *{es}* → *{shp}*.",
]

# Plantilla cuando el usuario REPITE un saludo o frase ya respondida
# (segundo turno con el MISMO tipo de consulta).
_TPL_REPETICION_SALUDO = [
    "Veo que vuelves a saludar 🌿. ¿Quieres que te muestre otras formas de saludar?",
    "Otro saludo. Si quieres, te puedo mostrar despedidas o agradecimientos en shipibo.",
    "¿Probamos algo distinto? Te puedo decir cómo despedirte o agradecer en shipibo.",
]

# Plantilla cuando el usuario escribe una palabra del CORPUS DE VOCABULARIO
# dentro de Conversar conmigo. Aquí no traducimos — redirigimos al apartado
# correcto para preservar la separación de funciones por sección.
_TPL_REDIRIGIR_VOCABULARIO = [
    "🌿 *{palabra}* en shipibo-konibo se relaciona con *{traduccion}*. "
    "Esa palabra forma parte del corpus de vocabulario, "
    "donde se trabaja con ejercicio bidireccional y pistas.",
    "💬 *{palabra}* ↔ *{traduccion}*. "
    "Hay un módulo dedicado al vocabulario en el menú lateral, "
    "donde esta palabra se practica con sus variantes.",
    "Te puedo decir que *{palabra}* corresponde a *{traduccion}*. "
    "En el módulo de Vocabulario se trabaja a fondo con pistas y modos de práctica.",
]

# Escalación cuando el usuario manda EXACTAMENTE el mismo texto tres veces
# o más seguidas. Ofrece variantes conversacionales sin empujar a otros módulos
# (el menú lateral del frontend está disponible si el usuario quiere cambiar).
_TPL_ESCALACION_REPETICION = [
    "Vi que escribiste *{texto}* varias veces. ¿Probamos otra cosa? "
    "Puedo enseñarte un saludo distinto o contarte algo de la cultura shipiba.",
    "Has repetido *{texto}* varias veces. Cambiemos un poco de aire: "
    "te puedo mostrar otra frase conversacional o una curiosidad cultural.",
    "Repetimos *{texto}* unas cuantas veces 🌱. "
    "¿Te animas con un saludo distinto, una despedida, o una curiosidad cultural?",
]

# Despedidas que el bot envía
_TPL_DESPEDIDA = [
    "🌿 *Kabanon* (hasta luego). Vuelve cuando quieras seguir aprendiendo shipibo.",
    "¡Eara jopariai! Hasta la próxima.",
    "Kabanon. Buen camino con el shipibo. 🌟",
]

# Plantillas de fallback (cuando no se reconoce nada).
# IMPORTANTE: no mencionar palabras del corpus de vocabulario como ejemplo
# para no invitar al usuario a hacer aquí lo que pertenece a otro apartado.
_TPL_FALLBACK = [
    "🤔 No reconocí eso. Aquí me dedico a saludos, despedidas, agradecimientos y curiosidades culturales.",
    "Esa frase no la conozco. Pruébame con un saludo en shipibo o pregúntame algo de la cultura.",
    "No te entendí. Te puedo enseñar frases conversacionales y curiosidades de la cultura shipiba.",
]

# Plantillas para cuando el usuario expresa intención de cambiar de actividad
# DENTRO del modo conversar (ej. "quiero aprender vocabulario", "cuéntame un
# cuento"). El bot reconoce el deseo con naturalidad pero NO da botón de
# salida: solo menciona que esos módulos están disponibles en el menú lateral.
# Esto preserva el aislamiento del modo sin sentirse rígido.
_TPL_INTENTO_CAMBIO_MODO = [
    "🌿 Aquí en Conversar trabajamos saludos, frases y temas culturales. "
    "Si lo que buscas es eso, lo encuentras en el menú lateral. "
    "¿Te enseño un saludo mientras tanto?",
    "Esa actividad tiene su propio módulo en el menú lateral 🌱. "
    "Aquí en Conversar puedo enseñarte saludos en shipibo o contarte algo "
    "de la cultura. ¿Por dónde empezamos?",
    "💬 Para esa práctica está su módulo en el menú lateral. "
    "Acá podemos charlar: saludos, despedidas, frases conversacionales o cultura.",
]

# Bienvenidas: una corta para entradas repetidas, una más rica la primera vez
_BIENVENIDA_PRIMERA = (
    "¡Jawekeskarin! 🌿 Soy *Pishico*, tu compañero para aprender shipibo. "
    "¿Por dónde quieres empezar?"
)
_BIENVENIDA_REPETIDA = (
    "Volviste a Conversar. ¿Qué te gustaría hacer ahora?"
)


def _es_despedida(texto: str) -> bool:
    """Detecta si el usuario está despidiéndose (es o shp)."""
    t = texto.lower().strip().rstrip(".!?¡¿")
    if t in _PALABRAS_DESPEDIDA:
        return True
    # Inicio del texto con palabra de despedida (ej. "chau, hasta mañana")
    primera = t.split()[0] if t.split() else ""
    return primera in _PALABRAS_DESPEDIDA


def _es_pregunta_cultural(texto: str) -> bool:
    """Heurística para detectar consultas culturales.

    Distingue una consulta cultural (que va al RAG / curiosidades) de:
      • Preguntas meta-lingüísticas ("cómo se dice", "cómo me despido") — esas
        se responden con el corpus de frases, NO con el RAG cultural.
      • Saludos en forma de pregunta ("¿cómo estás?") — esos se responden como
        frase conversacional, no como pregunta cultural.
    """
    t = texto.lower().strip()

    # Si es claramente una pregunta meta-lingüística sobre cómo decir algo,
    # NO es cultural. La responde el detector meta-lingüístico (bloque 4.5).
    if _detectar_pregunta_metalinguistica(t) is not None:
        return False

    # Saludos/cortesía en forma de pregunta ("¿cómo estás?") tampoco son
    # culturales — son frases conversacionales que ya están en el corpus.
    saludos_pregunta = {
        "cómo estás", "como estas", "qué tal", "que tal",
        "cómo está", "como esta", "cómo van", "como van",
    }
    if any(s in t for s in saludos_pregunta):
        return False

    # Detección positiva normal: tiene signos de pregunta o empieza con
    # palabra interrogativa.
    if "?" in t or "¿" in t:
        return True
    palabras_pregunta = {"qué", "que", "cómo", "como", "por", "dónde", "donde",
                         "cuál", "cual", "explícame", "explicame",
                         "cuéntame", "cuentame", "háblame", "hablame"}
    primera = t.split()[0] if t.split() else ""
    return primera in palabras_pregunta


# ─────────────────────────────────────────────────────────────────────────────
# Preguntas culturales rotativas
# ─────────────────────────────────────────────────────────────────────────────
# Lista de preguntas variadas para el botón "Algo de la cultura". Cada vez que
# se construye el botón se elige una al azar, así el usuario obtiene contenido
# diverso del PDF de cosmovisión en lugar del mismo chunk siempre.
_PREGUNTAS_CULTURALES = [
    "¿qué es el kené?",
    "cuéntame sobre la ayahuasca",
    "¿quién es Ronin?",
    "explícame la cosmovisión shipiba",
    "¿qué son los icaros?",
    "háblame de los espíritus del agua",
    "¿qué significa onanya?",
    "cuéntame sobre el río Ucayali",
]


def _pregunta_cultural_aleatoria() -> str:
    """Devuelve una pregunta cultural al azar de la lista variada."""
    return _random.choice(_PREGUNTAS_CULTURALES)


# ─────────────────────────────────────────────────────────────────────────────
# Detector meta-lingüístico: "cómo me despido", "cómo saludo", etc.
# ─────────────────────────────────────────────────────────────────────────────
# Si el usuario pregunta CÓMO decir algo (despedirse, saludar, agradecer...),
# no es una pregunta cultural sino una consulta sobre el idioma. La respuesta
# debe venir del corpus de frases conversacionales, no del PDF de cosmovisión.
#
# Mapa: lista de palabras-clave → categoría del corpus que las responde.
# La categoría debe coincidir con las del Excel frases_conversacionales.xlsx.
_MAPEO_METALINGUISTICO = [
    # (palabras_clave, categoría_corpus, plantilla_intro)
    (("despedir", "despido", "despedida", "decir adiós", "decir adios"),
     "despedida",
     "Para despedirte en shipibo se dice"),
    (("saludar", "saludo", "saludos", "decir hola"),
     "saludo",
     "Para saludar en shipibo se dice"),
    (("agradecer", "agradezco", "decir gracias", "gracias"),
     "agradecer",
     "Para agradecer en shipibo se dice"),
    (("disculpar", "disculparse", "pedir disculpa", "pedir disculpas"),
     "disculpa",
     "Para disculparte en shipibo se dice"),
    (("pedir ayuda", "ayudar"),
     "ayuda",
     "Para pedir ayuda en shipibo se dice"),
    (("presentar", "presentarme", "soy", "mi nombre"),
     "identidad",
     "Para presentarte en shipibo se dice"),

    # ── Categorías incorporadas con el corpus del hablante (2026) ──
    # Los nombres de categoría coinciden EXACTAMENTE con los que genera
    # split_traducciones.py y, por tanto, con la columna `categoria` del
    # Excel frases_conversacionales.xlsx. Si cambia uno, hay que cambiar el otro.
    # Nota: la categoría "disculpa" de arriba todavía no tiene frases con
    # traducción en el corpus; cae al fallback honesto del bloque 4.5 hasta
    # que el hablante provea equivalentes de "lo siento / perdón".
    (("por favor", "ser cortes", "ser cortés", "pedir amablemente",
      "decir por favor", "con cortesia", "con cortesía"),
     "cortesia",
     "Para pedir algo con cortesía en shipibo se dice"),
    (("expresar emocion", "expresar emoción", "expresar emociones",
      "como me siento", "cómo me siento", "decir que estoy",
      "expresar lo que siento", "estoy triste", "estoy feliz",
      "estoy contento", "estoy alegre", "mis emociones"),
     "emocion",
     "Para expresar cómo te sientes en shipibo puedes decir"),
    (("afirmar", "decir que si", "decir que sí", "estar de acuerdo",
      "confirmar algo", "como afirmo", "cómo afirmo"),
     "afirmacion",
     "Para afirmar o decir que sí en shipibo se dice"),
    (("negar", "decir que no", "como niego", "cómo niego", "rechazar algo"),
     "negacion",
     "Para negar o decir que no en shipibo se dice"),
]


def _detectar_pregunta_metalinguistica(texto: str):
    """
    Detecta si el usuario está preguntando 'cómo se dice X' o 'cómo me despido'
    y devuelve la categoría del corpus que responde, o None si no aplica.

    Returns:
        (categoria, plantilla_intro) si es meta-lingüística, None si no lo es.
    """
    t = texto.lower().strip() if texto else ""
    if not t:
        return None
    for palabras_clave, categoria, plantilla in _MAPEO_METALINGUISTICO:
        if any(p in t for p in palabras_clave):
            return (categoria, plantilla)
    return None


# Patrones de intención de traducción explícita. Capturan la palabra objetivo
# en el grupo 1. Cubren las formulaciones más naturales que un alumno usaría
# en el modo conversación libre. Se mantienen sin tildes en los patrones
# porque trabajamos contra un texto sin normalizar (lowercase manual).
_PATRONES_TRADUCCION = [
    # "traduce X" / "tradúceme X" / "traducime X" / "traducir X" / "cómo se traduce X"
    r"(?:traduce(?:me)?|traducime|traducir(?:me)?|c[oó]mo se traduce)\s+(?:la\s+palabra\s+)?([\wáéíóúñ\-]+)",
    # "cómo se dice X" / "cómo digo X"
    r"(?:c[oó]mo\s+(?:se\s+)?(?:dice|digo))\s+([\wáéíóúñ\-]+)",
    # "qué significa X" / "qué quiere decir X" / "qué es X" (último más laxo)
    r"qu[eé]\s+(?:significa|quiere\s+decir)\s+([\wáéíóúñ\-]+)",
    # "dime el significado de X" / "cuál es el significado de X"
    r"(?:dime|d[ií]me|cu[aá]l\s+es)\s+(?:el\s+)?significado\s+de\s+([\wáéíóúñ\-]+)",
    # "X en español" / "X en shipibo" / "X en castellano"
    r"([\wáéíóúñ\-]+)\s+en\s+(?:espa[ñn]ol|shipibo(?:-konibo)?|castellano)",
]

# Palabras a descartar: stopwords, artículos, pronombres muy cortos. Si el
# regex capturó algo así es ruido (no es la palabra que el alumno quiere).
_STOPWORDS_TRADUCCION = {
    "que", "lo", "la", "el", "tu", "yo", "mi", "su", "se", "es",
    "una", "un", "los", "las", "esta", "este", "ese", "esa", "eso",
    "como", "cómo", "esto", "algo", "palabra", "shipibo", "konibo",
    "espanol", "español", "castellano", "ingles", "inglés",
}


def _extraer_palabra_a_traducir(texto: str) -> Optional[str]:
    """
    Detecta intención de traducción explícita y extrae la palabra objetivo.

    Devuelve la palabra (en minúsculas, sin signos) o None si el texto no
    expresa una intención clara de traducción.

    Ejemplos de match:
      "traduce sol"           → "sol"
      "qué significa jene"    → "jene"
      "cómo se dice agua"     → "agua"
      "jene en español"       → "jene"
      "dime el significado de yapa" → "yapa"
    """
    import re as _re
    if not texto:
        return None
    t = texto.lower().strip()
    # Quitar signos finales para que no entren al grupo capturado
    t = _re.sub(r"[¿?¡!.,]", " ", t).strip()
    for patron in _PATRONES_TRADUCCION:
        m = _re.search(patron, t)
        if not m:
            continue
        palabra = m.group(1).strip()
        if len(palabra) < 2:
            continue
        if palabra in _STOPWORDS_TRADUCCION:
            continue
        return palabra
    return None


# Plantillas para respuesta de traducción explícita (modo Conversar). Se
# rotan para evitar respuestas idénticas en intentos consecutivos.
_TPL_TRADUCCION_OK = (
    "🌿 **{palabra}** se traduce como **{traduccion}**.",
    "🌿 La traducción de **{palabra}** es **{traduccion}**.",
    "🌿 En shipibo-konibo, **{palabra}** ↔ **{traduccion}**.",
)

_TPL_TRADUCCION_NO_ENCONTRADA = (
    "📚 No tengo la traducción de **{palabra}** en mi corpus actual. "
    "Conozco un grupo de palabras de uso cotidiano agrupadas en seis "
    "categorías. ¿Quieres probar con otra palabra o explorar el módulo "
    "**Aprender Vocabulario**?",
    "📚 La palabra **{palabra}** no está en mi corpus por ahora. "
    "Puedes intentar con palabras de naturaleza, animales, colores, "
    "cuerpo, objetos o números.",
)


# Palabras-disparador que indican que el usuario quiere cambiar a otro módulo.
# Lista mantenible sin necesidad de reentrenar el NLU. Se chequea ANTES del
# fallback genérico para responder con un mensaje natural en lugar de
# "no te entendí".
_DISPARADORES_CAMBIO_MODO = {
    # Vocabulario
    "vocabulario", "vocab", "palabras", "palabra nueva",
    "aprender palabras", "más palabras", "mas palabras",
    "ejercicio", "ejercicios", "práctica", "practica",
    # Cuento
    "cuento", "cuentos", "historia", "historias", "relato", "relatos",
    "narración", "narracion", "leer un cuento",
}


def _es_intento_cambio_modo(texto: str) -> bool:
    """
    Detecta si el usuario está pidiendo cambiar a vocabulario o cuento desde
    dentro del modo conversar. Heurística por palabras-clave: simple, robusta
    y mantenible sin reentrenar el NLU.

    Diseño: el bot NO redirige automáticamente, solo reconoce el intento y
    responde con naturalidad mencionando que esos módulos están en el menú
    lateral. La salida real la decide el usuario haciendo clic en la barra.
    """
    if not texto:
        return False
    t = normalizar(texto)
    # Match por contención: cubre "quiero vocabulario", "dame un cuento", etc.
    return any(palabra in t for palabra in _DISPARADORES_CAMBIO_MODO)


def _botones_default():
    """Botones genéricos del modo conversar.
    Solo contienen acciones propias de esta sección: saludos, cultura y
    despedidas. NO ofrecen rutas a otros módulos; la salida hacia
    Vocabulario o Cuento es decisión del usuario desde el menú lateral
    del frontend.
    """
    return [
        {"title": "Dime un saludo",     "payload": "Hola"},
        {"title": "Algo de la cultura", "payload": _pregunta_cultural_aleatoria()},
        {"title": "Cómo me despido",    "payload": "¿cómo me despido en shipibo?"},
    ]


def _botones_tras_saludo():
    """Tras un saludo respondido, ofrecer SOLO opciones conversacionales."""
    return [
        {"title": "Otro saludo",         "payload": "Buenas tardes"},
        {"title": "Cómo despedirme",     "payload": "Hasta luego"},
        {"title": "Algo de la cultura",  "payload": _pregunta_cultural_aleatoria()},
    ]


def _botones_tras_despedida():
    """Tras despedida no insistimos: solo un botón discreto para volver."""
    return [
        {"title": "Volver", "payload": "/conversar"},
    ]


def _botones_redirigir_vocabulario():
    """Cuando el usuario escribe una palabra del corpus de vocabulario.
    Solo opciones conversacionales: el frontend ya tiene el menú lateral
    para que el usuario decida si quiere cambiar al módulo de Vocabulario.
    """
    return [
        {"title": "Dame un saludo",     "payload": "Hola"},
        {"title": "Algo de la cultura", "payload": _pregunta_cultural_aleatoria()},
        {"title": "Cómo me despido",    "payload": "¿cómo me despido en shipibo?"},
    ]


def _categoria_de_palabra(palabra_es: str) -> Optional[str]:
    """Encuentra a qué categoría pertenece una palabra del corpus."""
    from corpus_loader import VOCABULARIO
    palabra_es = palabra_es.lower().strip()
    for cat, palabras in VOCABULARIO.items():
        for p in palabras:
            if p["es"].lower() == palabra_es or p["shp"].lower() == palabra_es:
                return cat
    return None


def _buscar_curiosidad_en_texto(texto: str) -> Optional[Dict[str, str]]:
    """
    Escanea el texto del usuario buscando palabras del corpus que tengan
    una curiosidad cultural asociada. Devuelve la primera encontrada, o None.

    Esta función es el reemplazo provisional del RAG en preguntas culturales:
    si el usuario pregunta "¿qué es el río en la cultura shipiba?", buscamos
    *río* en el índice de curiosidades. Cuando el RAG real esté disponible,
    esta función se sustituye por una llamada al retriever sin tocar el run().
    """
    if not _curiosidades_disponibles():
        return None
    try:
        from curiosidades_loader import CURIOSIDADES
    except ImportError:
        return None
    texto_norm = normalizar(texto)
    tokens = set(texto_norm.split())
    for palabra, opciones in CURIOSIDADES.items():
        if not opciones:
            continue
        if palabra in tokens or palabra in texto_norm:
            return _random.choice(opciones)
    return None


# ── Helpers de variedad y detección de repetición ─────────────────────────

def _formatear_sin_repetir(
    plantillas: List[str],
    ultima_respuesta: Optional[str],
    **kwargs,
) -> str:
    """
    Elige una plantilla al azar, formatea con kwargs, y evita devolver
    un mensaje idéntico al último que el bot envió (ultima_respuesta_bot).

    Necesario porque `random.choice` puro puede elegir la misma plantilla
    dos turnos seguidos, lo que se siente robótico cuando el usuario manda
    el mismo input dos veces.
    """
    if not plantillas:
        return ""
    posibles = list(plantillas)
    _random.shuffle(posibles)
    for tpl in posibles:
        try:
            mensaje = tpl.format(**kwargs)
        except (KeyError, IndexError):
            continue
        if mensaje != (ultima_respuesta or ""):
            return mensaje
    return plantillas[0].format(**kwargs)


def _texto_de_evento_user(evento: Dict[str, Any]) -> str:
    """
    Extrae el texto real del usuario de un evento 'user'.
    En modo conversar el frontend envía el payload
    /conversar{"texto_usuario": "..."} y el texto real vive en la entidad
    texto_usuario; fuera de conversar, vive en parse_data.text.
    """
    if not evento or evento.get("event") != "user":
        return ""
    parse = evento.get("parse_data") or {}
    for ent in parse.get("entities") or []:
        if ent.get("entity") == "texto_usuario" and ent.get("value"):
            return str(ent["value"])
    return str(parse.get("text") or evento.get("text") or "")


def _veces_repetido_input(tracker, texto_norm: str, max_check: int = 6) -> int:
    """
    Cuenta cuántos turnos user CONSECUTIVOS (incluyendo el actual) coinciden
    con texto_norm. Se mira hacia atrás hasta encontrar un turno distinto
    o agotar max_check.
    """
    if not texto_norm:
        return 0
    eventos_user = [
        e for e in (tracker.events or []) if e.get("event") == "user"
    ][-max_check:]
    veces = 0
    for e in reversed(eventos_user):
        otro = normalizar(_texto_de_evento_user(e))
        if otro and otro == texto_norm:
            veces += 1
        else:
            break
    return veces


def _botones_escalacion():
    """Botones que ofrece la escalación tras 3+ repeticiones del mismo input.
    Solo opciones conversacionales para romper el bucle sin forzar al usuario
    a cambiar de módulo (el menú lateral del frontend está disponible si lo
    desea).
    """
    return [
        {"title": "Otro saludo",        "payload": "Buenas tardes"},
        {"title": "Algo de la cultura", "payload": _pregunta_cultural_aleatoria()},
        {"title": "Despedirme",         "payload": "¿cómo me despido en shipibo?"},
    ]


class ActionResponderConversacion(Action):
    """
    Maneja el modo conversación libre con respuestas naturalizadas.

    Pipeline de detección (en orden de prioridad):
      1. Despedida (chau / kabanon / etc.) → cerrar con saludo respetuoso
      2. "Otra palabra de <categoría>" → dar palabra aleatoria de la categoría
      3. Palabra del corpus (54 palabras) → traducir + (eventualmente) curiosidad
      4. Frase del loader conversacional → responder con equivalencia
      5. Pregunta con interrogante → respuesta del RAG (placeholder por ahora)
      6. Fallback con plantilla aleatoria
    """

    def name(self) -> Text:
        return "action_responder_conversacion"

    def run(self, dispatcher, tracker, domain):
        import json, re as _re

        # ── Recuperar texto del usuario (3 fuentes en cascada) ──────────────
        texto = (tracker.get_slot("texto_usuario") or "").strip()
        if not texto:
            for ent in (tracker.latest_message or {}).get("entities", []):
                if ent.get("entity") == "texto_usuario":
                    texto = (ent.get("value") or "").strip()
                    if texto:
                        break
        if not texto:
            raw = (tracker.latest_message or {}).get("text", "") or ""
            m = _re.search(r"\{.*\}", raw)
            if m:
                try:
                    data = json.loads(m.group(0))
                    texto = str(data.get("texto_usuario", "") or "").strip()
                except (json.JSONDecodeError, ValueError):
                    pass

        # Sin texto → bienvenida (entrada al modo)
        if not texto:
            return self._bienvenida(dispatcher, tracker)

        ultima = tracker.get_slot("ultima_intencion_conv") or ""
        ultima_respuesta = tracker.get_slot("ultima_respuesta_bot") or ""

        # ── 1. Despedida ────────────────────────────────────────────────────
        # Tiene precedencia incluso sobre escalación: si quiere cerrar, cerramos.
        if _es_despedida(texto):
            mensaje = _formatear_sin_repetir(_TPL_DESPEDIDA, ultima_respuesta)
            dispatcher.utter_message(text=mensaje, buttons=_botones_tras_despedida())
            return self._cerrar(mensaje, "despedida")

        # ── 2. Escalación por repetición de input idéntico ──────────────────
        # Si el usuario manda EXACTAMENTE el mismo texto 3 veces seguidas,
        # cambiamos de táctica: ofrecemos rutas concretas para salir del bucle.
        texto_norm = normalizar(texto)
        veces = _veces_repetido_input(tracker, texto_norm)
        if veces >= 3:
            mensaje = _formatear_sin_repetir(
                _TPL_ESCALACION_REPETICION, ultima_respuesta, texto=texto
            )
            dispatcher.utter_message(text=mensaje, buttons=_botones_escalacion())
            return self._cerrar(mensaje, "escalacion")

        # ── 2.5 INTENCIÓN EXPLÍCITA DE TRADUCCIÓN ───────────────────────────
        # "traduce X", "qué significa X", "cómo se dice X", "X en español", etc.
        # Va ANTES del bloque 3 (palabra suelta) porque captura formulaciones
        # de varias palabras que el match exacto del corpus no detectaría.
        # Si la palabra está en el corpus, respondemos con la traducción
        # directa. Si no, mensaje honesto que no inventa traducciones.
        palabra_a_traducir = _extraer_palabra_a_traducir(texto)
        if palabra_a_traducir:
            traduccion_explicita = _traducir_corpus(normalizar(palabra_a_traducir))
            if traduccion_explicita:
                mensaje = _formatear_sin_repetir(
                    _TPL_TRADUCCION_OK, ultima_respuesta,
                    palabra=palabra_a_traducir,
                    traduccion=traduccion_explicita,
                )
            else:
                mensaje = _formatear_sin_repetir(
                    _TPL_TRADUCCION_NO_ENCONTRADA, ultima_respuesta,
                    palabra=palabra_a_traducir,
                )
            dispatcher.utter_message(text=mensaje, buttons=_botones_default())
            return self._cerrar(mensaje, "traduccion")

        # ── 3. PALABRA DEL CORPUS DE VOCABULARIO → respuesta natural ────────
        # Aislamiento de funciones suave: las palabras del corpus de vocabulario
        # se practican en el módulo Aprender Vocabulario, pero aquí en Conversar
        # respondemos la traducción directamente y mencionamos el módulo SIN
        # botón de salida. El usuario decide si quiere cambiar desde el menú
        # lateral del frontend.
        traduccion = _traducir_corpus(texto_norm)
        if traduccion and len(texto_norm.split()) <= 2:
            palabra_mostrada = texto.strip()
            mensaje = _formatear_sin_repetir(
                _TPL_REDIRIGIR_VOCABULARIO, ultima_respuesta,
                palabra=palabra_mostrada, traduccion=traduccion,
            )
            dispatcher.utter_message(text=mensaje, buttons=_botones_redirigir_vocabulario())
            return self._cerrar(mensaje, "redirigir_vocab")

        # ── 4. Frase del corpus conversacional (saludos, agradecer, etc.) ──
        if _frases_conv_disponibles():
            match = _buscar_frase_conv(texto)
            if match:
                # Si el usuario ya hizo un saludo y vuelve a saludar → variar.
                es_saludo = match["categoria"] in ("saludo", "despedida")
                if es_saludo and ultima == "frase":
                    mensaje = _formatear_sin_repetir(
                        _TPL_REPETICION_SALUDO, ultima_respuesta
                    )
                    dispatcher.utter_message(text=mensaje, buttons=_botones_tras_saludo())
                    return self._cerrar(mensaje, "frase")

                es_limpio  = match["es"].rstrip(".!?¡¿")
                shp_limpio = match["shp"].rstrip(".!?¡¿")

                plantillas = (
                    _TPL_FRASE_SHP if match["idioma_detectado"] == "shp"
                    else _TPL_FRASE_ES
                )
                mensaje = _formatear_sin_repetir(
                    plantillas, ultima_respuesta, es=es_limpio, shp=shp_limpio
                )

                botones = _botones_tras_saludo() if es_saludo else _botones_default()
                dispatcher.utter_message(text=mensaje, buttons=botones)
                return self._cerrar(mensaje, "frase")

        # ── 4.5. Pregunta meta-lingüística ───────────────────────────────────
        # "¿cómo me despido?", "¿cómo saludo?", "¿cómo digo gracias?", etc.
        # Estas NO van al RAG cultural sino al corpus de frases conversacionales.
        # Devolvemos una frase aleatoria de la categoría correspondiente, con
        # un marco textual que explique la traducción.
        meta = _detectar_pregunta_metalinguistica(texto)
        if meta is not None:
            categoria, intro = meta
            frases_cat = _frases_por_categoria(categoria)
            if frases_cat:
                frase = _random.choice(frases_cat)
                es_limpio  = frase["es"].rstrip(".!?¡¿")
                shp_limpio = frase["shp"].rstrip(".!?¡¿")
                mensaje = (
                    f"💬 {intro} **{shp_limpio}**, "
                    f"que en español significa _{es_limpio}_."
                )
                dispatcher.utter_message(text=mensaje, buttons=_botones_default())
                return self._cerrar(mensaje, "meta_linguistica")
            # Si el corpus no tiene esa categoría (raro), caemos al placeholder
            # honesto en lugar de mandar al RAG cultural.
            mensaje = (
                f"💬 Aún no tengo frases de esa categoría en mi corpus, "
                f"pero te puedo enseñar saludos, agradecimientos o despedidas."
            )
            dispatcher.utter_message(text=mensaje, buttons=_botones_default())
            return self._cerrar(mensaje, "meta_linguistica")

        # ── 5. Pregunta cultural ────────────────────────────────────────────
        # Búsqueda en cascada (orden de calidad): primero la curaduría manual
        # (texto pulido y corto), después el RAG sobre el PDF (cobertura total
        # pero párrafos más largos), y al final un placeholder honesto si nada
        # responde con suficiente relevancia.
        if _es_pregunta_cultural(texto):
            # 5a) Curiosidad curada a mano (mayor calidad textual)
            cur = _buscar_curiosidad_en_texto(texto)
            if cur:
                mensaje = f"💡 _{cur['texto']}_"
                if cur.get("fuente"):
                    mensaje += f"\n\n_Fuente: {cur['fuente']}_"
                dispatcher.utter_message(text=mensaje, buttons=_botones_default())
                return self._cerrar(mensaje, "pregunta_cultural")

            # 5b) RAG sobre PDF de cosmovisión (cobertura completa del documento)
            if _rag_disponible():
                respuesta_rag = _rag_responder(texto)
                if respuesta_rag:
                    dispatcher.utter_message(text=respuesta_rag, buttons=_botones_default())
                    return self._cerrar(respuesta_rag, "pregunta_cultural")

            # 5c) Fallback honesto: ni curaduría ni RAG dieron respuesta relevante
            mensaje = (
                "📚 Esa es una pregunta sobre cultura shipiba interesante, "
                "pero no encontré información específica sobre eso en mis fuentes. "
                "Puedo enseñarte saludos en shipibo o contarte sobre otros temas culturales."
            )
            dispatcher.utter_message(text=mensaje, buttons=_botones_default())
            return self._cerrar(mensaje, "pregunta_cultural")

        # ── 6. Intento de cambio de modo (opción B sin NLU adicional) ───────
        # Si el usuario expresa querer hacer otra actividad (vocabulario o
        # cuento) DENTRO del modo conversar, no caemos en fallback genérico:
        # reconocemos el intento y le recordamos que esos módulos están en el
        # menú lateral del frontend. Sin botón de salida — la decisión es suya.
        if _es_intento_cambio_modo(texto):
            mensaje = _formatear_sin_repetir(_TPL_INTENTO_CAMBIO_MODO, ultima_respuesta)
            dispatcher.utter_message(text=mensaje, buttons=_botones_default())
            return self._cerrar(mensaje, "intento_cambio_modo")

        # ── 7. Fallback con variabilidad ────────────────────────────────────
        mensaje = _formatear_sin_repetir(_TPL_FALLBACK, ultima_respuesta)
        dispatcher.utter_message(text=mensaje, buttons=_botones_default())
        return self._cerrar(mensaje, "fallback")

    # ── Helpers internos ──────────────────────────────────────────────────

    def _bienvenida(self, dispatcher, tracker) -> List[EventType]:
        """Mensaje al entrar al modo conversación."""
        # Si ya hubo una bienvenida en esta sesión, usar la versión corta
        ya_estuvo = tracker.get_slot("ultima_intencion_conv") is not None
        mensaje = _BIENVENIDA_REPETIDA if ya_estuvo else _BIENVENIDA_PRIMERA
        dispatcher.utter_message(text=mensaje, buttons=_botones_default())
        return [
            SlotSet("flujo_actual", "conversar"),
            SlotSet("texto_usuario", None),
            SlotSet("ultima_intencion_conv", "bienvenida"),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]

    def _cerrar(self, mensaje: str, tipo_intencion: str) -> List[EventType]:
        """Eventos comunes al cerrar cualquier respuesta del modo conversación."""
        return [
            SlotSet("flujo_actual", "conversar"),
            SlotSet("texto_usuario", None),
            SlotSet("ultima_intencion_conv", tipo_intencion),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]

# ════════════════════════════════════════════════════════════════════════════
# SUB-MODO "APRENDER" (responde a la observación del jurado: separar aprender
# de evaluar). Al entrar a una categoría, el usuario elige entre APRENDER las
# palabras (ver es/shp, sin test) o EVALUARSE (el test que ya existía).
# NO modifica el test (actividad_form) ni ActionIniciarVocabulario.
# ════════════════════════════════════════════════════════════════════════════

# Carpeta de imágenes servida por server.py en /images/<archivo>.
# Coincide con IMAGES_DIR de server.py (raíz_proyecto/images).
_IMAGES_DIR = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "images"
)
_IMG_EXTS = (".webp", ".jpg", ".jpeg", ".png")
_IMG_URL_BASE = "/images/"


def _slug_palabra(es: str) -> str:
    """'árbol' -> 'arbol'. Sin tildes, minúsculas, sin espacios."""
    t = str(es).lower().strip()
    t = "".join(
        ch for ch in unicodedata.normalize("NFD", t)
        if unicodedata.category(ch) != "Mn"
    )
    return t.replace(" ", "_")


def _ruta_imagen_palabra(es: str):
    """URL de la imagen si el archivo existe en images/, o None.
    Transición a imágenes SIN tocar código: basta dejar images/<slug>.webp."""
    slug = _slug_palabra(es)
    for ext in _IMG_EXTS:
        if _os.path.isfile(_os.path.join(_IMAGES_DIR, slug + ext)):
            return _IMG_URL_BASE + slug + ext
    return None


def _palabra_get(palabra, clave, default=""):
    """Accede a un campo de palabra, sea dict o tupla del corpus."""
    if isinstance(palabra, dict):
        return palabra.get(clave, default)
    # fallback si el corpus usa otra estructura
    return getattr(palabra, clave, default)


def _tarjeta_aprendizaje(palabra, indice: int, total: int) -> str:
    es = _palabra_get(palabra, "es", "")
    shp = _palabra_get(palabra, "shp", "")
    return (
        f"📖 *Aprendiendo* ({indice}/{total})\n\n"
        f"En español:  **{es}**\n"
        f"En shipibo:  **{shp}**\n\n"
        f"Memorízala y cuando estés listo, continúa. 🌿"
    )


def _resolver_categoria_vocab(tracker: Tracker):
    """Extrae la categoría del payload/texto del usuario."""
    CATS = [c for c in VOCABULARIO.keys() if VOCABULARIO.get(c)]
    slot_cat = tracker.get_slot("categoria_actual")
    ents = (tracker.latest_message or {}).get("entities", [])
    texto = (tracker.latest_message or {}).get("text", "").lower()
    from_payload = any(
        e.get("entity") == "categoria_actual" for e in ents
    ) or '"categoria_actual"' in texto
    if from_payload and slot_cat in CATS:
        return slot_cat
    for cat in CATS:
        if cat in texto:
            return cat
    if slot_cat in CATS:
        return slot_cat
    return None


class ActionOfrecerModoVocab(Action):
    """Al entrar a una categoría, ofrece elegir entre APRENDER y EVALUARSE."""
    def name(self) -> Text:
        return "action_ofrecer_modo_vocab"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[EventType]:

        if not corpus_disponible():
            dispatcher.utter_message(
                text="⚠️ El corpus no está disponible. Verificá *palabras.xlsx*."
            )
            return []

        categoria = _resolver_categoria_vocab(tracker)
        if categoria is None:
            CATS = [c for c in VOCABULARIO.keys() if VOCABULARIO.get(c)]
            categoria = CATS[0] if CATS else None
        if not categoria or not VOCABULARIO.get(categoria):
            dispatcher.utter_message(text="No encontré esa categoría. Probemos con otra.")
            return []

        total = len(VOCABULARIO.get(categoria, []))
        dispatcher.utter_message(
            text=(
                f"Entraste a la categoría *{categoria}* ({total} palabras).\n\n"
                f"¿Qué prefieres hacer?"
            ),
            buttons=[
                {"title": "📖 Aprender primero",
                 "payload": f'/aprender_palabras{{"categoria_actual":"{categoria}"}}'},
                {"title": "✍️ Evaluarme",
                 "payload": f'/aprender_vocabulario{{"categoria_actual":"{categoria}"}}'},
            ],
        )
        return [
            SlotSet("flujo_actual", "vocabulario"),
            SlotSet("categoria_actual", categoria),
            SlotSet("fragmento_actual", 0),  # reinicia el cursor de aprendizaje
        ]


class ActionAprenderVocabulario(Action):
    """Recorre las palabras de la categoría como tarjetas de estudio (es↔shp)."""
    def name(self) -> Text:
        return "action_aprender_vocabulario"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[EventType]:

        if not corpus_disponible():
            dispatcher.utter_message(text="⚠️ El corpus no está disponible.")
            return []

        categoria = _resolver_categoria_vocab(tracker) or tracker.get_slot("categoria_actual")
        palabras = VOCABULARIO.get(categoria, [])
        if not palabras:
            dispatcher.utter_message(text="No encontré palabras en esa categoría.")
            return []

        try:
            idx = int(tracker.get_slot("fragmento_actual") or 0)
        except (TypeError, ValueError):
            idx = 0

        ents = (tracker.latest_message or {}).get("entities", [])
        cambio_categoria = any(e.get("entity") == "categoria_actual" for e in ents)
        if cambio_categoria:
            idx = 0

        if idx >= len(palabras):
            dispatcher.utter_message(
                text=(
                    f"¡Terminaste de aprender la categoría *{categoria}*! 🎉\n"
                    f"¿Quieres poner a prueba lo que aprendiste?"
                ),
                buttons=[
                    {"title": "✍️ Evaluarme ahora",
                     "payload": f'/aprender_vocabulario{{"categoria_actual":"{categoria}"}}'},
                    {"title": "📖 Aprender otra categoría",
                     "payload": "/aprender_vocabulario"},
                ],
            )
            return [SlotSet("fragmento_actual", 0)]

        palabra = palabras[idx]
        texto = _tarjeta_aprendizaje(palabra, idx + 1, len(palabras))
        img_url = _ruta_imagen_palabra(_palabra_get(palabra, "es", ""))

        es_ultima = (idx + 1) >= len(palabras)
        botones = []
        if es_ultima:
            # Última palabra: solo un botón para pasar al test (sin redundancia).
            botones.append({
                "title": "✅ Terminar y evaluarme",
                "payload": f'/aprender_vocabulario{{"categoria_actual":"{categoria}"}}',
            })
        else:
            botones.append({"title": "Siguiente palabra ▶", "payload": "/aprender_palabras"})
            botones.append({"title": "✍️ Saltar al test",
                            "payload": f'/aprender_vocabulario{{"categoria_actual":"{categoria}"}}'})

        if img_url:
            dispatcher.utter_message(text=texto, image=img_url, buttons=botones)
        else:
            dispatcher.utter_message(text=texto, buttons=botones)

        return [
            SlotSet("flujo_actual", "vocabulario"),
            SlotSet("categoria_actual", categoria),
            SlotSet("fragmento_actual", idx + 1),
            SlotSet("palabra_actual", _palabra_get(palabra, "es")),
        ]


# ════════════════════════════════════════════════════════════════════════════
# PALABRAS CLAVE DEL CUENTO (responde al jurado: leer bilingüe primero, evaluar
# después). Antes de empezar un cuento, muestra las palabras shipibo que el
# usuario va a encontrar, con su traducción. Luego el cuento corre con sus
# preguntas e intentos SIN CAMBIOS. Solo aparece al arrancar desde cero.
# ════════════════════════════════════════════════════════════════════════════

# Mini-diccionario de respaldo: traducciones de palabras que aparecen en los
# cuentos pero NO están en el vocabulario principal. Solo se usa para mostrar
# el "= español" en la pantalla de palabras clave; no afecta la evaluación ni
# el vocabulario practicable. Traducciones tomadas de los archivos paralelos
# es/shp originales de cada cuento.
_TRAD_CUENTOS = {
    "ikonrake":   "gracias",
    "nenobi":     "aquí",
    "nexai":      "amarrar",
    "tenten":     "jalar",
    "atsa xeati": "tomar masato",
    "teeti":      "trabajar",
    "xoboati":    "construir la casa",
    "poxati":     "tumbar",
    "repinti":    "puerto",
}


class ActionPalabrasClaveCuento(Action):
    """Muestra la lista bilingüe de palabras clave antes de iniciar el cuento."""
    def name(self) -> Text:
        return "action_palabras_clave_cuento"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[EventType]:

        if not cuentos_disponibles():
            dispatcher.utter_message(
                text="⚠️ No hay cuentos cargados."
            )
            return []

        cuento_elegido = tracker.get_slot("cuento_actual") or _cuento_id_desde_entidades(tracker)
        cuento_id = cuento_elegido or CUENTO_PREDETERMINADO
        if not cuento_por_id(cuento_id):
            cuento_id = CUENTO_PREDETERMINADO

        # Si el usuario ya tiene progreso en este cuento, NO mostrar palabras
        # clave otra vez: ir directo a retomar el cuento.
        ya_empezado = ultimo_fragmento_acertado(tracker.sender_id, cuento_id)
        if ya_empezado is not None:
            return [FollowupAction("action_iniciar_cuento")]

        # Recolectar las palabras meta (respuesta_esperada) de todos los fragmentos
        total = _cuento_total_fragmentos(cuento_id) or 0
        palabras = []
        for i in range(total):
            frag = _cuento_fragmento(cuento_id, i)
            if not frag:
                continue
            resp = frag.get("respuesta_esperada")
            if resp:
                # Buscar traducción: primero en el vocabulario (DICCIONARIO es↔shp);
                # si no está, usar el mini-diccionario de respaldo de palabras de
                # cuentos (_TRAD_CUENTOS) para que TODA palabra muestre su "= es".
                es = DICCIONARIO.get(resp.lower(), "")
                if not es:
                    es = _TRAD_CUENTOS.get(resp.lower().strip(), "")
                palabras.append((resp, es))

        titulo = _cuento_titulo(cuento_id)

        if not palabras:
            # El cuento no tiene palabras meta: arrancar directo
            return [FollowupAction("action_iniciar_cuento")]

        # Construir la tarjeta de palabras clave
        lineas = [f"📖 Antes de leer **{titulo}**, estas son las palabras clave "
                  f"que vas a encontrar:\n"]
        for shp, es in palabras:
            if es:
                lineas.append(f"• **{shp}** = {es}")
            else:
                lineas.append(f"• **{shp}**")
        lineas.append("\nLéelas con calma. Luego, durante el cuento, te las "
                      "preguntaré. 🌿")

        dispatcher.utter_message(
            text="\n".join(lineas),
            buttons=[
                {"title": "📖 Comenzar el cuento", "payload": "/iniciar_cuento"},
            ],
        )
        return [SlotSet("cuento_actual", cuento_id)]