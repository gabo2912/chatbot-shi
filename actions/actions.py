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
from rasa_sdk.events import SlotSet, EventType, FollowupAction

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
)



CUENTO_PESCADOR: List[Dict[str, Any]] = [
    {
        "texto": (
            "Había una vez un pescador shipibo llamado Ronin. 🛶\n"
            "Cada mañana iba al río con su canoa para pescar."
        ),
        "pregunta": "En este fragmento aparece el río. ¿Cómo se dice 'agua' en shipibo?",
        "respuesta_esperada": "jene",     # nat_001 — grafía canónica validada
        "ayuda": "El río está lleno de *agua*. En shipibo, *agua* se dice 'jene'.",
    },
    {
        "texto": (
            "Ronin pescaba muchos peces para alimentar a su familia.\n"
            "Un día vio un pez grande y brillante."
        ),
        "pregunta": "¿Cómo se dice 'pez' en shipibo?",
        "respuesta_esperada": "waka",     # ani_001 — grafía canónica validada (era 'yapa')
        "ayuda": "Recuerda: el animal que Ronin pesca se llama 'waka' en shipibo.",
    },
    {
        "texto": (
            "El pez le habló a Ronin: 'Si me dejas vivir, te enseñaré "
            "las palabras antiguas de la selva.'"
        ),
        "pregunta": "Ronin usa sus *manos* para soltar al pez. ¿Cómo se dice 'mano' en shipibo?",
        "respuesta_esperada": "meno",     # cuer_001 — grafía canónica validada (era 'meken')
        "ayuda": "La parte del cuerpo con la que agarramos cosas se dice 'meno'.",
    },
    {
        "texto": (
            "Ronin aceptó. Así aprendió muchas palabras antiguas y compartió "
            "el conocimiento con su pueblo. 🌿"
        ),
        "pregunta": None,  # último fragmento sin pregunta
        "respuesta_esperada": None,
        "ayuda": None,
    },
]


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

        # Caso ayuda/pista: dar pista real, no mensaje genérico
        if es_ayuda or (
            intent in {"pedir_ayuda", "expresar_emocion"}
            and confianza >= 0.75
            and len(tokens) > 1
        ):
            categoria = tracker.get_slot("categoria_actual") or "naturaleza"
            palabra_es = tracker.get_slot("palabra_actual") or ""
            info = encontrar_palabra(categoria, palabra_es)
            pista = info.get("pista", "Piensa en el contexto de la palabra.")

            dispatcher.utter_message(
                text=(
                    f"💡 Pista para **{palabra_es}**: {pista}\n"
                    f"¿Cómo se dice **{palabra_es}** en shipibo?"
                )
            )
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
        # o la primera disponible como fallback.
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

        # 3) Fallback: primera categoría disponible
        if categoria is None:
            categoria = CATEGORIAS_VALIDAS[0]

        palabras = VOCABULARIO.get(categoria, [])
        if not palabras:
            dispatcher.utter_message(
                text=f"No encontré palabras en la categoría *{categoria}*. Probemos con otra."
            )
            return []

        palabra = palabras[0]

        mensaje = (
            f"¡Vamos a practicar vocabulario! 🌿\n"
            f"Categoría: *{categoria}*\n\n"
            f"¿Cómo se dice **{palabra['es']}** en shipibo?"
        )
        dispatcher.utter_message(text=mensaje, buttons=[
            {"title": "Dame una pista", "payload": "dame una pista"},
            {"title": "Saltar palabra",  "payload": "continuar"},
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
        mensaje = f"Siguiente palabra:\n\n¿Cómo se dice **{nueva['es']}** en shipibo?"
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

        info = encontrar_palabra(categoria, palabra_es)
        esperada_shp = info.get("shp", "")
        variantes    = info.get("variantes", [])
        resultado = evaluar_respuesta(texto, esperada_shp, variantes)

        eventos_base = [SlotSet("respuesta_actividad", None)]

        if resultado == "correcto":
            registrar_intento(
                tracker.sender_id, categoria, palabra_es, esperada_shp, "correcto", intentos + 1
            )
            mensaje = (
                f"¡Excelente! ✅ '**{palabra_es}**' en shipibo es '**{esperada_shp}**'.\n"
                f"¿Seguimos?"
            )
            dispatcher.utter_message(text=mensaje, buttons=[
                {"title": "Siguiente palabra", "payload": "continuar"},
                {"title": "Pausar", "payload": "pausa"},
            ])
            return eventos_base + [SlotSet("ultima_respuesta_bot", mensaje)]

        if resultado == "parcial":
            registrar_intento(
                tracker.sender_id, categoria, palabra_es, esperada_shp, "parcial", intentos + 1
            )
            mensaje = (
                f"Casi. 🤏 Te acercaste, pero la respuesta exacta es '**{esperada_shp}**'.\n"
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
            mensaje = (
                f"No es esa. 💡 Pista: {info.get('pista', 'piensa en su uso cotidiano.')}\n"
                f"¿Cómo se dice **{palabra_es}** en shipibo?"
            )
            dispatcher.utter_message(text=mensaje)
            return eventos_base + [
                SlotSet("intentos_palabra", intentos),
                SlotSet("ultima_respuesta_bot", mensaje),
                FollowupAction("actividad_form"),
            ]

        registrar_intento(
            tracker.sender_id, categoria, palabra_es, esperada_shp, "incorrecto", intentos + 1
        )
        mensaje = (
            f"No te preocupes. La respuesta correcta es '**{esperada_shp}**'. 🌱\n"
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
        info = encontrar_palabra(categoria, palabra_es)
        pista = info.get("pista", "Piensa en su uso cotidiano.")
        mensaje = (
            f"💡 Pista para **{palabra_es}**: {pista}\n"
            f"¿Cómo se dice **{palabra_es}** en shipibo?"
        )
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
        palabra = tracker.get_slot("palabra_actual")
        if palabra:
            mensaje = f"Volvamos a lo nuestro. ¿Cómo se dice **{palabra}** en shipibo?"
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
        idx = 0
        frag = CUENTO_PESCADOR[idx]
        mensaje = f"📖 **El pescador shipibo** — Parte {idx + 1}\n\n{frag['texto']}"
        if frag.get("pregunta"):
            mensaje += f"\n\n{frag['pregunta']}"
        else:
            mensaje += "\n\nEscribe 'continuar' para seguir."
        dispatcher.utter_message(text=mensaje)
        return [
            SlotSet("flujo_actual", "cuento"),
            SlotSet("fragmento_actual", float(idx)),
            SlotSet("palabra_actual", None),
            SlotSet("categoria_actual", None),
            SlotSet("intentos_palabra", 0),
            SlotSet("respuesta_actividad", None),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]


class ActionSiguienteFragmento(Action):
    def name(self) -> Text:
        return "action_siguiente_fragmento"

    def run(self, dispatcher, tracker, domain):
        idx = int(tracker.get_slot("fragmento_actual") or 0) + 1
        if idx >= len(CUENTO_PESCADOR):
            mensaje = "Has terminado el cuento. 🌟 ¿Quieres practicar vocabulario ahora?"
            dispatcher.utter_message(text=mensaje)
            return [
                SlotSet("flujo_actual", "ninguno"),
                SlotSet("fragmento_actual", 0),
                SlotSet("ultima_respuesta_bot", mensaje),
            ]
        frag = CUENTO_PESCADOR[idx]
        mensaje = f"📖 Parte {idx + 1}\n\n{frag['texto']}"
        if frag.get("pregunta"):
            mensaje += f"\n\n{frag['pregunta']}"
        else:
            mensaje += "\n\nEscribe 'continuar' para seguir."
        dispatcher.utter_message(text=mensaje)
        return [
            SlotSet("fragmento_actual", float(idx)),
            SlotSet("ultima_respuesta_bot", mensaje),
        ]


class ActionEvaluarRespuestaCuento(Action):
    def name(self) -> Text:
        return "action_evaluar_respuesta_cuento"

    def run(self, dispatcher, tracker, domain):
        texto = (tracker.latest_message or {}).get("text", "")
        idx = int(tracker.get_slot("fragmento_actual") or 0)
        frag = CUENTO_PESCADOR[idx]
        esperada = frag.get("respuesta_esperada") or ""

        if not esperada:
            dispatcher.utter_message(text="Sigamos con el cuento. Escribe 'continuar'.")
            return []

        resultado = evaluar_respuesta(texto, esperada)
        registrar_fragmento_cuento(
            tracker.sender_id, "pescador_shipibo", idx, resultado == "correcto"
        )
        if resultado == "correcto":
            mensaje = f"¡Muy bien! ✅ '{esperada}' es la palabra correcta. Sigamos con la historia."
        elif resultado == "parcial":
            mensaje = f"Te acercaste. 🤏 La respuesta es '{esperada}'. Continuemos."
        else:
            mensaje = f"No exactamente. La palabra que buscábamos era '{esperada}'. 🌱 Sigamos."
        dispatcher.utter_message(text=mensaje, buttons=[
            {"title": "Continuar historia", "payload": "continuar"},
        ])
        return [SlotSet("ultima_respuesta_bot", mensaje)]


class ActionDarAyudaCuento(Action):
    def name(self) -> Text:
        return "action_dar_ayuda_cuento"

    def run(self, dispatcher, tracker, domain):
        idx = int(tracker.get_slot("fragmento_actual") or 0)
        frag = CUENTO_PESCADOR[idx]
        ayuda = frag.get("ayuda") or "Lee el fragmento con calma y piensa en el contexto."
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
        frag = CUENTO_PESCADOR[idx]
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
        if idx < len(CUENTO_PESCADOR):
            frag = CUENTO_PESCADOR[idx]
            if frag.get("pregunta"):
                mensaje = f"Volvamos al cuento. {frag['pregunta']}"
            else:
                mensaje = "Volvamos al cuento. Escribe 'continuar' para seguir."
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