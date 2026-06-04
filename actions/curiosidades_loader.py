"""
curiosidades_loader.py — Carga curiosidades culturales asociadas al corpus.

Las curiosidades se extraen del PDF cultural "Shipibo: territorio, historia y
cosmovisión" (174 páginas) y se almacenan en actions/corpus/curiosidades.json.
Cada palabra del corpus puede tener 0, 1 o 2 curiosidades asociadas.

Estructura del JSON:
{
  "_meta": { ... },
  "curiosidades": {
    "agua": [
      {"texto": "...", "fuente": "..."},
      {"texto": "...", "fuente": "..."}
    ],
    ...
  }
}

DISEÑO COMPATIBLE CON RAG (futuro):
Cuando se active el RAG sobre el PDF completo, este loader podrá reemplazarse
por una función que haga `rag.buscar(palabra)` y devuelva chunks similares en
formato idéntico. La signatura pública (`obtener_curiosidad`) se mantiene.

Uso:
    from curiosidades_loader import obtener_curiosidad
    dato = obtener_curiosidad("agua")  # → dict | None
"""

import os
import json
import random
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

CURIOSIDADES_PATH = os.path.join(
    os.path.dirname(__file__), "corpus", "curiosidades.json"
)

# Probabilidad por defecto de mostrar una curiosidad cuando hay una disponible.
# 0.30 = aproximadamente 1 de cada 3 traducciones agrega un dato cultural.
# Suficiente para que aporte color sin volverse repetitivo.
PROBABILIDAD_CURIOSIDAD = 0.30


def _cargar_curiosidades() -> Dict[str, Any]:
    """Lee el JSON desde disco. Devuelve dict vacío si falla."""
    if not os.path.exists(CURIOSIDADES_PATH):
        logger.warning(
            "curiosidades_loader: no se encontró %s. "
            "El bot funcionará pero sin curiosidades culturales.",
            CURIOSIDADES_PATH
        )
        return {}
    try:
        with open(CURIOSIDADES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Soporta tanto formato directo (dict de palabras) como envuelto en _meta
        if "curiosidades" in data:
            return data["curiosidades"]
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.error("curiosidades_loader: error al cargar %s: %s",
                     CURIOSIDADES_PATH, e)
        return {}


# ── Carga única al importar el módulo ────────────────────────────────────────
CURIOSIDADES: Dict[str, Any] = _cargar_curiosidades()
_carga_ok = bool(CURIOSIDADES)
logger.info("curiosidades_loader: %d palabras con curiosidad cultural",
            len(CURIOSIDADES))


# ── API pública ───────────────────────────────────────────────────────────────

def curiosidades_disponibles() -> bool:
    """True si el JSON se cargó correctamente y hay datos."""
    return _carga_ok


def obtener_curiosidad(
    palabra_es: str,
    probabilidad: float = PROBABILIDAD_CURIOSIDAD,
    forzar: bool = False,
) -> Optional[Dict[str, str]]:
    """
    Devuelve una curiosidad para la palabra en español, con cierta probabilidad.

    Args:
        palabra_es: palabra en español (la clave del corpus, ej. "agua")
        probabilidad: chance de devolver una curiosidad si está disponible (0-1)
        forzar: si es True, ignora la probabilidad y devuelve una curiosidad
                si existe (útil para testing o respuestas explícitas)

    Returns:
        dict con keys "texto" y "fuente", o None si:
          - la palabra no tiene curiosidades, o
          - el dado aleatorio decidió no mostrarla
    """
    if not palabra_es:
        return None

    clave = palabra_es.lower().strip()
    opciones = CURIOSIDADES.get(clave)
    if not opciones:
        return None

    if not forzar and random.random() > probabilidad:
        return None

    return random.choice(opciones)


def total_palabras_con_curiosidad() -> int:
    """Cuenta cuántas palabras del corpus tienen al menos una curiosidad."""
    return len(CURIOSIDADES)
