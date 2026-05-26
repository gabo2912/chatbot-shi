"""
corpus_loader.py — Carga dinámica del corpus léxico shipibo-konibo.

Reemplaza el vocabulario embebido en actions.py por una fuente de datos
externa (Excel curado y validado por especialista lingüístico).

Estructura esperada del Excel (palabras.xlsx):
  Columna A: id propuesto (ej. nat_001)
  Columna B: es            (palabra en español)
  Columna C: shp           (forma canónica shipibo, validada)
  Columnas D+: variantes   (grafías alternativas aceptadas, o "no hay")

Ubicación del Excel:
  actions/corpus/palabras.xlsx

Uso en actions.py:
  from corpus_loader import VOCABULARIO, DICCIONARIO, encontrar_palabra, siguiente_palabra
"""

import os
import unicodedata
from typing import Dict, List, Any, Optional

# ── Ruta al corpus ────────────────────────────────────────────────────────────
CORPUS_PATH = os.path.join(os.path.dirname(__file__), "corpus", "palabras.xlsx")

# ── Mapeo prefijo → nombre de categoría ──────────────────────────────────────
CATEGORIA_MAP: Dict[str, str] = {
    "nat":  "naturaleza",
    "ani":  "animales",
    "cuer": "cuerpo",
    "col":  "colores",
    "obj":  "objetos",
}

# ── Pistas pedagógicas por palabra en español ─────────────────────────────────
# Usadas en ActionDarPistaVocab y ValidateActividadForm para dar contexto
# al aprendiz cuando falla la actividad.
PISTAS: Dict[str, str] = {
    # naturaleza
    "agua":      "Sin esto no podemos vivir. Corre en los ríos de la Amazonía.",
    "sol":       "Aparece en el cielo durante el día y nos da luz y calor.",
    "árbol":     "Crece alto en la selva. Tiene tronco, ramas y hojas.",
    "luna":      "La vemos brillar en el cielo por las noches.",
    "río":       "Corriente de agua donde los shipibos navegan y pescan.",
    "viento":    "Lo sentimos cuando el aire se mueve entre los árboles.",
    "lluvia":    "Cae del cielo y hace crecer las plantas de la selva.",
    "nube":      "Flota en el cielo y a veces trae lluvia.",
    "fuego":     "Se usa para cocinar. Produce calor y luz.",
    "tierra":    "El suelo sobre el que caminamos y donde crecen las plantas.",
    "noche":     "El momento en que el sol se va y aparecen las estrellas.",
    # animales
    "pez":       "Animal que vive en el agua y se pesca en el río.",
    "tortuga":   "Tiene caparazón duro. Vive en ríos y en la tierra.",
    "perro":     "Animal doméstico que ladra y acompaña a las familias.",
    "mono":      "Trepa los árboles con agilidad. Muy común en la selva.",
    "ave":       "Tiene plumas y puede volar por el cielo.",
    "serpiente": "Reptil sin patas que se arrastra por el suelo.",
    "jaguar":    "El felino más grande de la Amazonía. Muy respetado.",
    "caimán":    "Reptil grande que vive en los ríos amazónicos.",
    "pato":      "Ave acuática que nada en ríos y lagunas.",
    "gallina":   "Ave de corral que pone huevos.",
    "mariposa":  "Insecto con alas de colores que vuela entre las flores.",
    "venado":    "Animal de cuatro patas con cuernos que vive en el bosque.",
    "gato":      "Animal doméstico pequeño que maúlla.",
    # cuerpo
    "mano":      "La usamos para escribir, agarrar y crear cosas.",
    "boca":      "Sirve para comer, hablar y sonreír.",
    "ojo":       "Con esto vemos el mundo que nos rodea.",
    "cabeza":    "La parte más alta de nuestro cuerpo.",
    "pie":       "La usamos para caminar y pararnos.",
    "corazón":   "Órgano que late dentro del pecho y bombea sangre.",
    "cabello":   "Crece en la cabeza. Puede ser largo o corto.",
    "diente":    "Lo usamos para masticar la comida.",
    "nariz":     "Con esto respiramos y olemos.",
    "oreja":     "Con esto escuchamos los sonidos.",
    "brazo":     "Une la mano con el hombro.",
    "pierna":    "La usamos para caminar y correr.",
    # colores
    "blanco":    "El color de las nubes y la leche.",
    "negro":     "El color de la oscuridad de la noche.",
    "azul":      "El color del cielo despejado y del agua profunda.",
    "rojo":      "El color de la sangre y del fuego.",
    "verde":     "El color de las plantas y la selva amazónica.",
    "amarillo":  "El color del sol y del oro.",
    "gris":      "Color intermedio entre el blanco y el negro.",
    "marrón":    "El color de la tierra y la madera.",
    "rosado":    "Color claro parecido al rojo, como algunas flores.",
    # objetos
    "canoa":     "Embarcación de madera que se usa para navegar en el río.",
    "casa":      "Lugar donde vive la familia shipibo.",
    "olla":      "Recipiente de barro que se usa para cocinar.",
    "tela":      "Material tejido que se usa para hacer ropa y artesanías.",
    "comida":    "Todo lo que comemos para vivir y crecer.",
    "ropa":      "Prendas que usamos para vestirnos.",
    "plato":     "Recipiente donde ponemos la comida para servirla.",
    "libro":     "Tiene páginas y contiene conocimiento escrito.",
    "escuela":   "Lugar donde vamos a aprender y estudiar.",
}


# ── Helpers internos ──────────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, sin puntuación, sin espacios extra."""
    t = texto.lower().strip()
    for c in ".,!?¿¡;:":
        t = t.replace(c, "")
    t = "".join(
        ch for ch in unicodedata.normalize("NFD", t)
        if unicodedata.category(ch) != "Mn"
    )
    return t.strip()


def _variante_valida(v: Any) -> bool:
    """
    Acepta una variante si es una cadena simple (sin paréntesis, sin
    descripciones largas) y tiene al menos 2 caracteres.
    Descarta: None, 'no hay', strings con '(', strings con más de 2 palabras.
    """
    if not v:
        return False
    v = str(v).strip()
    if not v or v.lower() == "no hay":
        return False
    if "(" in v:                           # tiene nota entre paréntesis
        return False
    palabras = v.split()
    if len(palabras) > 2:                  # frase demasiado larga
        return False
    if len(v) < 2:                         # token demasiado corto
        return False
    return True


# ── Carga principal ───────────────────────────────────────────────────────────

def _cargar_desde_excel(path: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Lee el Excel y devuelve:
    {
      "naturaleza": [
        {
          "id": "nat_001",
          "es": "agua",
          "shp": "jene",
          "variantes": ["umpas"],   # grafías alternativas normalizadas
          "pista": "Sin esto no podemos vivir..."
        },
        ...
      ],
      "animales": [...],
      ...
    }
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ImportError(
            "openpyxl no está instalado. Ejecutá: pip install openpyxl"
        )

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No se encontró el corpus en '{path}'.\n"
            "Copiá el archivo palabras.xlsx a actions/corpus/palabras.xlsx"
        )

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active

    vocab: Dict[str, List[Dict[str, Any]]] = {
        nombre: [] for nombre in CATEGORIA_MAP.values()
    }

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue

        id_p   = str(row[0]).strip()
        es     = str(row[1]).strip() if row[1] else ""
        shp    = str(row[2]).strip() if row[2] else ""

        if not es or not shp:
            continue

        # Variantes: columnas 3 en adelante
        variantes_raw = row[3:]
        variantes = [
            _normalizar(str(v))
            for v in variantes_raw
            if _variante_valida(v)
        ]
        # Deduplicar y quitar la forma canónica si ya está
        shp_norm = _normalizar(shp)
        variantes = list(dict.fromkeys(
            v for v in variantes if v and v != shp_norm
        ))

        prefix = id_p.split("_")[0]
        categoria = CATEGORIA_MAP.get(prefix)
        if not categoria:
            continue

        entrada: Dict[str, Any] = {
            "id":       id_p,
            "es":       es,
            "shp":      shp,
            "variantes": variantes,
            "pista":    PISTAS.get(es, "Piensa en el contexto de esta palabra."),
        }
        vocab[categoria].append(entrada)

    wb.close()
    return vocab


def _construir_diccionario(
    vocab: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, str]:
    """
    Diccionario plano bidireccional para traducción rápida es↔shp.
    Incluye variantes como llaves adicionales apuntando al español.

    Nota: josho (blanco/rosado) y joshin (verde/marrón) son duplicados
    naturales del corpus; el diccionario guarda el último cargado.
    La desambiguación la hace el evaluador según la palabra_actual del slot.
    """
    d: Dict[str, str] = {}
    for palabras in vocab.values():
        for p in palabras:
            es_n  = _normalizar(p["es"])
            shp_n = _normalizar(p["shp"])
            d[es_n]  = p["shp"]   # agua  → jene
            d[shp_n] = p["es"]    # jene  → agua
            for v in p.get("variantes", []):
                v_n = _normalizar(v)
                if v_n and v_n not in d:
                    d[v_n] = p["es"]  # umpas → agua
    return d


# ── Carga única al importar el módulo ────────────────────────────────────────

try:
    VOCABULARIO: Dict[str, List[Dict[str, Any]]] = _cargar_desde_excel(CORPUS_PATH)
    DICCIONARIO: Dict[str, str] = _construir_diccionario(VOCABULARIO)
    _carga_ok = True
except Exception as _e:
    import logging
    logging.getLogger(__name__).error(
        "corpus_loader: no se pudo cargar el corpus desde Excel. "
        "Usando vocabulario vacío. Error: %s", _e
    )
    VOCABULARIO = {nombre: [] for nombre in CATEGORIA_MAP.values()}
    DICCIONARIO = {}
    _carga_ok = False


# ── API pública ───────────────────────────────────────────────────────────────

def corpus_disponible() -> bool:
    """True si el corpus se cargó correctamente desde el Excel."""
    return _carga_ok


def categorias() -> List[str]:
    """Lista de nombres de categoría disponibles."""
    return [c for c, palabras in VOCABULARIO.items() if palabras]


def palabras_de(categoria: str) -> List[Dict[str, Any]]:
    """Devuelve todas las palabras de una categoría."""
    return VOCABULARIO.get(categoria, [])


def encontrar_palabra(categoria: str, es: str) -> Dict[str, Any]:
    """Busca una palabra por su forma en español dentro de una categoría."""
    for p in VOCABULARIO.get(categoria, []):
        if p["es"] == es:
            return p
    return {}


def siguiente_palabra(categoria: str, palabra_actual: str) -> Dict[str, Any]:
    """
    Devuelve la siguiente palabra en la categoría (rota al inicio
    si llega al final).
    """
    palabras = VOCABULARIO.get(categoria, [])
    if not palabras:
        return {}
    for i, p in enumerate(palabras):
        if p["es"] == palabra_actual:
            return palabras[(i + 1) % len(palabras)]
    return palabras[0]


def traducir(palabra: str) -> str:
    """
    Traducción rápida ES⇄SHP usando el diccionario plano.
    Devuelve cadena vacía si no encuentra la palabra.
    """
    return DICCIONARIO.get(_normalizar(palabra), "")
