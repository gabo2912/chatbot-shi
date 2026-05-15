"""
Acciones personalizadas para el chatbot educativo shipibo-konibo.

NOTA: Este archivo contiene STUBS FUNCIONALES con un vocabulario y un cuento
embebidos para poder probar las stories de inmediato. Cuando los módulos
reales del proyecto estén listos (corpus curado, traductor, evaluador,
corrector ortográfico), reemplazar las funciones marcadas con `# TODO: real`.
"""

from typing import Any, Text, Dict, List
import unicodedata

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, EventType


# ════════════════════════════════════════════════════════════════════
# DATOS DE EJEMPLO (reemplazar por corpus curado real)
# ════════════════════════════════════════════════════════════════════

VOCABULARIO: Dict[str, List[Dict[str, str]]] = {
    "naturaleza": [
        {"es": "agua",  "shp": "jene",  "pista": "Sin esto no podemos vivir, fluye en los ríos."},
        {"es": "sol",   "shp": "bari",  "pista": "Está en el cielo durante el día y da calor."},
        {"es": "árbol", "shp": "jiwi",  "pista": "Crece alto en la selva y tiene hojas."},
    ],
    "animales": [
        {"es": "pez",   "shp": "yapa",   "pista": "Vive en el agua y se pesca en el río."},
        {"es": "perro", "shp": "ochiti", "pista": "Animal doméstico que ladra."},
    ],
    "cuerpo": [
        {"es": "mano", "shp": "meken", "pista": "La usamos para escribir y agarrar cosas."},
        {"es": "ojo",  "shp": "bero",  "pista": "Con esto vemos el mundo."},
    ],
}

# Diccionario plano bidireccional (construido desde el vocabulario)
DICCIONARIO: Dict[str, str] = {}
for _palabras in VOCABULARIO.values():
    for _p in _palabras:
        DICCIONARIO[_p["es"].lower()] = _p["shp"]
        DICCIONARIO[_p["shp"].lower()] = _p["es"]


CUENTO_PESCADOR: List[Dict[str, Any]] = [
    {
        "texto": (
            "Había una vez un pescador shipibo llamado Ronin. 🛶\n"
            "Cada mañana iba al río con su canoa para pescar."
        ),
        "pregunta": "En este fragmento aparece el río. ¿Cómo se dice 'agua' en shipibo?",
        "respuesta_esperada": "jene",
        "ayuda": "El río está lleno de *agua*. En shipibo, *agua* se dice 'jene'.",
    },
    {
        "texto": (
            "Ronin pescaba muchos peces para alimentar a su familia.\n"
            "Un día vio un pez grande y brillante."
        ),
        "pregunta": "¿Cómo se dice 'pez' en shipibo?",
        "respuesta_esperada": "yapa",
        "ayuda": "Recuerda: el animal que Ronin pesca se llama 'yapa' en shipibo.",
    },
    {
        "texto": (
            "El pez le habló a Ronin: 'Si me dejas vivir, te enseñaré "
            "las palabras antiguas de la selva.'"
        ),
        "pregunta": "Ronin usa sus *manos* para soltar al pez. ¿Cómo se dice 'mano' en shipibo?",
        "respuesta_esperada": "meken",
        "ayuda": "La parte del cuerpo con la que agarramos cosas se dice 'meken'.",
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


def evaluar_respuesta(usuario: str, esperada: str) -> str:
    """Devuelve 'correcto' | 'parcial' | 'incorrecto'.

    TODO: real — reemplazar por módulo de evaluación contextual + corrector
    ortográfico cuando esté listo.
    """
    if not usuario or not esperada:
        return "incorrecto"
    u, e = normalizar(usuario), normalizar(esperada)
    if u == e:
        return "correcto"
    if e in u.split() or e in u:
        return "parcial"
    return "incorrecto"


def traducir(palabra: str) -> str:
    """Traducción simple ES⇄SHP por diccionario.

    TODO: real — reemplazar por módulo traductor del proyecto.
    """
    return DICCIONARIO.get(normalizar(palabra), "")


def encontrar_palabra(categoria: str, es: str) -> Dict[str, str]:
    """Busca el diccionario completo de una palabra en una categoría."""
    for p in VOCABULARIO.get(categoria, []):
        if p["es"] == es:
            return p
    return {}


def siguiente_palabra(categoria: str, palabra_actual: str) -> Dict[str, str]:
    """Devuelve la siguiente palabra en la categoría (rota al inicio si llega al final)."""
    palabras = VOCABULARIO.get(categoria, [])
    if not palabras:
        return {}
    for i, p in enumerate(palabras):
        if p["es"] == palabra_actual:
            return palabras[(i + 1) % len(palabras)]
    return palabras[0]


# ════════════════════════════════════════════════════════════════════
# ACCIONES — HISTORIA 1: VOCABULARIO
# ════════════════════════════════════════════════════════════════════

class ActionIniciarVocabulario(Action):
    def name(self) -> Text:
        return "action_iniciar_vocabulario"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[EventType]:
        categoria = "naturaleza"
        palabra = VOCABULARIO[categoria][0]
        mensaje = (
            f"¡Vamos a practicar vocabulario! 🌿\n"
            f"Categoría: *{categoria}*\n\n"
            f"¿Cómo se dice **{palabra['es']}** en shipibo?"
        )
        dispatcher.utter_message(text=mensaje)
        return [
            SlotSet("flujo_actual", "vocabulario"),
            SlotSet("categoria_actual", categoria),
            SlotSet("palabra_actual", palabra["es"]),
            SlotSet("intentos_palabra", 0),
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
            SlotSet("ultima_respuesta_bot", mensaje),
        ]


class ActionEvaluarRespuestaVocab(Action):
    def name(self) -> Text:
        return "action_evaluar_respuesta_vocab"

    def run(self, dispatcher, tracker, domain):
        texto = (tracker.latest_message or {}).get("text", "")
        categoria = tracker.get_slot("categoria_actual") or "naturaleza"
        palabra_es = tracker.get_slot("palabra_actual") or ""
        intentos = int(tracker.get_slot("intentos_palabra") or 0)

        info = encontrar_palabra(categoria, palabra_es)
        esperada_shp = info.get("shp", "")
        resultado = evaluar_respuesta(texto, esperada_shp)

        if resultado == "correcto":
            mensaje = (
                f"¡Excelente! ✅ '**{palabra_es}**' en shipibo es '**{esperada_shp}**'.\n"
                f"¿Continuamos con otra palabra?"
            )
            dispatcher.utter_message(text=mensaje)
            return [SlotSet("ultima_respuesta_bot", mensaje)]

        if resultado == "parcial":
            mensaje = (
                f"Casi. 🤏 Te acercaste, pero la respuesta exacta es '**{esperada_shp}**'.\n"
                f"¿Quieres intentar con otra palabra?"
            )
            dispatcher.utter_message(text=mensaje)
            return [SlotSet("ultima_respuesta_bot", mensaje)]

        # incorrecto
        intentos += 1
        if intentos < 2:
            mensaje = (
                f"No es esa. 💡 Pista: {info.get('pista', 'piensa en su uso cotidiano.')}\n"
                f"¿Cómo se dice **{palabra_es}** en shipibo?"
            )
        else:
            mensaje = (
                f"No te preocupes. La respuesta correcta es '**{esperada_shp}**'. 🌱\n"
                f"¿Pasamos a otra palabra?"
            )
        dispatcher.utter_message(text=mensaje)
        return [
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
        dispatcher.utter_message(text="¿Quieres aprender vocabulario o un cuento?")
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
        if resultado == "correcto":
            mensaje = f"¡Muy bien! ✅ '{esperada}' es la palabra correcta. Sigamos con la historia."
        elif resultado == "parcial":
            mensaje = f"Te acercaste. 🤏 La respuesta es '{esperada}'. Continuemos."
        else:
            mensaje = f"No exactamente. La palabra que buscábamos era '{esperada}'. 🌱 Sigamos."
        dispatcher.utter_message(text=mensaje)
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
        traduccion = traducir(palabra)
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
