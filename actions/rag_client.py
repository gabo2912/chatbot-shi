"""
rag_client.py — Cliente HTTP del servidor RAG independiente.

Este módulo reemplaza al `rag_loader.py` anterior (que cargaba chromadb
en proceso). Ahora el RAG vive en otro servicio HTTP (rag-service/) que
corre en su propio venv con LangChain libre, sin restricciones de Rasa.

INTERFAZ PÚBLICA (IDÉNTICA a rag_loader.py):
- responder_cultural_simple(query) → str | None
- rag_disponible() → bool

Esto significa que actions.py NO necesita modificar su lógica del bloque 5
"Pregunta cultural": solo cambia el import.

POLÍTICAS:
- Solo usa `requests` (ya está instalado por Rasa, cero deps nuevas).
- Si el servicio RAG está caído o no responde, devuelve None / False;
  el bot cae al placeholder textual y el usuario nunca ve un error.
- Configuración por variable de entorno RAG_SERVICE_URL (default: http://127.0.0.1:8001).
- Timeout corto (5s) para que el bot no se cuelgue si el RAG está lento.

Para activar el Camino A (LLM): no requiere cambios acá; basta con
configurar RAG_MODO_DEFAULT=llm en el .env del servicio rag-service.
Ver docs/rag_camino_a.md.
"""

import os
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Configuración ────────────────────────────────────────────────────────────
# URL base del servicio RAG. Si no se setea env var, usa el default local.
RAG_SERVICE_URL = os.getenv("RAG_SERVICE_URL", "http://127.0.0.1:8001").rstrip("/")

# Timeout en segundos para las llamadas HTTP. Corto a propósito para que el
# bot nunca se cuelgue esperando al RAG; si tarda más de esto, fallback.
RAG_TIMEOUT = float(os.getenv("RAG_TIMEOUT", "5.0"))


# ── Estado interno (caché del health check) ──────────────────────────────────
# Cacheamos el resultado del health check por unos segundos para no consultar
# en cada request del usuario; el flag se actualiza al primer fallo.
_disponible_cache: Optional[bool] = None
_disponible_check_intentado: bool = False


def rag_disponible() -> bool:
    """
    Verifica si el servicio RAG está vivo y disponible.
    Hace una sola llamada HTTP a /health (cacheada en memoria).

    Returns:
        True si el servicio respondió OK al /health, False si no.
    """
    global _disponible_cache, _disponible_check_intentado

    if _disponible_check_intentado:
        return bool(_disponible_cache)

    _disponible_check_intentado = True
    try:
        resp = requests.get(f"{RAG_SERVICE_URL}/health", timeout=RAG_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            ok = data.get("rag_disponible", False)
            _disponible_cache = ok
            if ok:
                logger.info("rag_client: servicio RAG disponible en %s", RAG_SERVICE_URL)
            else:
                logger.warning(
                    "rag_client: servicio RAG arriba pero degradado (%s): %s",
                    RAG_SERVICE_URL, data.get("detalle", "sin detalle")
                )
            return ok
        _disponible_cache = False
        logger.warning("rag_client: /health respondió %d", resp.status_code)
        return False
    except requests.exceptions.ConnectionError:
        _disponible_cache = False
        logger.warning(
            "rag_client: servicio RAG no responde en %s. "
            "Iniciá el servidor con: bash scripts/arrancar.sh",
            RAG_SERVICE_URL
        )
        return False
    except Exception as e:
        _disponible_cache = False
        logger.warning("rag_client: error al verificar /health: %s", e)
        return False


def responder_cultural_simple(query: str) -> Optional[str]:
    """
    Camino B: pide respuesta cultural al servicio RAG.

    Misma interfaz que la función homónima del rag_loader.py anterior, así
    actions.py no necesita modificar su lógica del bloque 5 'Pregunta cultural'.

    Args:
        query: pregunta del usuario en lenguaje natural

    Returns:
        Texto listo para mostrar al usuario, o None si:
          - el servicio no respondió o respondió error
          - el servicio no encontró match relevante (respuesta=null en el JSON)
    """
    if not query or not query.strip():
        return None

    try:
        resp = requests.post(
            f"{RAG_SERVICE_URL}/buscar",
            json={"query": query, "k": 1, "modo": "simple"},
            timeout=RAG_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("rag_client: /buscar respondió %d", resp.status_code)
            return None
        data = resp.json()
        return data.get("respuesta")  # puede ser str o None
    except requests.exceptions.ConnectionError:
        logger.warning("rag_client: servicio RAG no responde en %s", RAG_SERVICE_URL)
        # Marcar como no disponible para evitar reintentos en esta sesión
        global _disponible_cache, _disponible_check_intentado
        _disponible_cache = False
        _disponible_check_intentado = True
        return None
    except requests.exceptions.Timeout:
        logger.warning("rag_client: timeout (>%.1fs) consultando RAG", RAG_TIMEOUT)
        return None
    except Exception as e:
        logger.warning("rag_client: error en /buscar: %s", e)
        return None


# ── Función para forzar recheck del health (útil en tests) ───────────────────

def _resetear_cache_disponibilidad():
    """Borra el caché del health check. Solo para testing."""
    global _disponible_cache, _disponible_check_intentado
    _disponible_cache = None
    _disponible_check_intentado = False
