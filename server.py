"""
server.py — Servidor proxy para el frontend de Pishico Bot.

Endpoints:
  GET  /                          → sirve index.html
  GET  /status                    → estado de Rasa
  GET  /progreso/<sender_id>      → progreso del usuario desde la DB (JSON)
  GET  /cuentos/<sender_id>       → catálogo de cuentos + progreso (JSON)
  POST /webhooks/rest/webhook     → proxy a Rasa

Uso:
    python3 server.py
"""

import json
import mimetypes
import sqlite3
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

RASA_WEBHOOK = "http://localhost:5005/webhooks/rest/webhook"
RASA_STATUS  = "http://localhost:5005/status"
PORT         = 8080

# El proxy puede estar fuera de la carpeta actions/, así que probamos varias ubicaciones
HERE         = Path(__file__).parent
HTML_FILE    = HERE / "index.html"
IMAGES_DIR   = HERE / "images"
DB_CANDIDATES = [
    HERE / "actions" / "progress.db",
    HERE / "progress.db",
    HERE.parent / "actions" / "progress.db",
]

CUENTOS_XLSX_CANDIDATES = [
    HERE / "actions" / "corpus" / "cuentos.xlsx",
    HERE / "corpus" / "cuentos.xlsx",
    HERE.parent / "actions" / "corpus" / "cuentos.xlsx",
]

# Metadatos visuales por cuento (emoji + descripción corta).
# Si en el futuro se agregan más cuentos, el frontend usa 📖 por defecto.
CUENTO_META = {
    "motelo_tigre": {
        "emoji": "🐢",
        "descripcion": "Una fábula sobre cómo el ingenio vence a la fuerza.",
    },
    "paujil_fiesta": {
        "emoji": "🦃",
        "descripcion": "El paujil descubre por qué tiene plumas blancas en la cola.",
    },
    "matrimonio_shipibo": {
        "emoji": "🏡",
        "descripcion": "Las dos pruebas que el yerno debía superar antes de casarse.",
    },
    "anciano_camungo": {
    "emoji": "🧓",
    "descripcion": "Un relato breve sobre pesca, naturaleza y sabiduría.",
    },
    "la_pesca": {
        "emoji": "🎣",
        "descripcion": "Una historia sobre la pesca y la vida cotidiana.",
    },
}

# Emojis por categoría — sumar entradas al añadir categorías nuevas
EMOJI_CAT = {
    "naturaleza": "🌿", "animales": "🦜", "cuerpo": "🫀",
    "colores": "🎨", "objetos": "🏺", "números": "🔢",
}

# Mapeo prefijo → categoría (debe estar sincronizado con corpus_loader.py)
_CATEGORIA_MAP = {
    "nat": "naturaleza", "ani": "animales", "cuer": "cuerpo",
    "col": "colores",    "obj": "objetos", "num": "números",
}

# Marcadores que indican ausencia de equivalente shipibo (palabras filtradas)
_MARCADORES_VACIO = {"no hay", "no_hay", "n/a", "-", ""}

# Localizar palabras.xlsx (mismas ubicaciones candidatas que cuentos.xlsx)
PALABRAS_XLSX_CANDIDATES = [
    HERE / "actions" / "corpus" / "palabras.xlsx",
    HERE / "corpus" / "palabras.xlsx",
    HERE.parent / "actions" / "corpus" / "palabras.xlsx",
]


def _palabras_xlsx_path():
    for c in PALABRAS_XLSX_CANDIDATES:
        if c.exists():
            return c
    return None


def _calcular_total_palabras():
    """
    Lee palabras.xlsx y cuenta palabras válidas por categoría.
    Reemplaza la constante hardcoded para que reflejar el corpus real.
    Si el Excel no se encuentra, usa fallback estático.
    """
    fallback = {
        "naturaleza": 11, "animales": 13, "cuerpo": 12,
        "colores": 6, "objetos": 9, "números": 10,
    }
    path = _palabras_xlsx_path()
    if not path:
        return fallback
    try:
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        ws = wb.active
        conteo = {cat: 0 for cat in _CATEGORIA_MAP.values()}
        for row in ws.iter_rows(min_row=2, values_only=True):
            id_ = str(row[0]).strip() if row and row[0] else ""
            if not id_:
                continue
            shp = str(row[2]).strip().lower() if len(row) > 2 and row[2] else ""
            if shp in _MARCADORES_VACIO:
                continue  # filtrar "no hay"
            prefix = id_.split("_", 1)[0] if "_" in id_ else ""
            cat = _CATEGORIA_MAP.get(prefix)
            if cat:
                conteo[cat] += 1
        wb.close()
        return {k: v for k, v in conteo.items() if v > 0}
    except Exception:
        return fallback


# Total dinámico computado al iniciar el servidor
TOTAL_PALABRAS = _calcular_total_palabras()


def _db_path():
    for c in DB_CANDIDATES:
        if c.exists():
            return c
    return None


def _leer_progreso(sender_id: str):
    """
    Lee la DB y devuelve progreso por categoría y por cuento, incluyendo
    desglose por nivel pedagógico (Capa 3 de la matriz):
      • nivel 2: lo logra al primer intento
      • nivel 1: lo logra con esfuerzo (varios intentos)
      • nivel 0: no lo logra (agotó intentos)
    """
    db = _db_path()
    if not db:
        return {"error": "DB no encontrada", "categorias": [], "cuentos": []}

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        # ── Resumen base por categoría ───────────────────────────────────
        cats_data = conn.execute("""
            SELECT
                categoria,
                COUNT(DISTINCT CASE WHEN resultado='correcto' THEN palabra_es END) AS dominadas,
                COUNT(DISTINCT palabra_es) AS vistas
            FROM progreso_vocabulario
            WHERE sender_id = ?
            GROUP BY categoria
        """, (sender_id,)).fetchall()

        # ── Desglose por nivel (toma el MEJOR nivel alcanzado por palabra) ─
        niveles_data = conn.execute("""
            SELECT
                categoria,
                SUM(CASE WHEN max_nivel=2 THEN 1 ELSE 0 END) AS nivel_2,
                SUM(CASE WHEN max_nivel=1 THEN 1 ELSE 0 END) AS nivel_1,
                SUM(CASE WHEN max_nivel=0 THEN 1 ELSE 0 END) AS nivel_0
            FROM (
                SELECT categoria, palabra_es, MAX(nivel) AS max_nivel
                FROM progreso_vocabulario
                WHERE sender_id = ?
                GROUP BY categoria, palabra_es
            )
            GROUP BY categoria
        """, (sender_id,)).fetchall()

        visto    = {r["categoria"]: r for r in cats_data}
        niveles  = {r["categoria"]: r for r in niveles_data}

        categorias = []
        for cat in TOTAL_PALABRAS.keys():
            r = visto.get(cat)
            n = niveles.get(cat)
            dom = r["dominadas"] if r else 0
            total = TOTAL_PALABRAS.get(cat, 0)
            categorias.append({
                "categoria":  cat,
                "emoji":      EMOJI_CAT.get(cat, "📚"),
                "dominadas":  dom,
                "total":      total,
                "porcentaje": round(dom / total * 100) if total else 0,
                "niveles": {
                    "2": (n["nivel_2"] if n else 0) or 0,
                    "1": (n["nivel_1"] if n else 0) or 0,
                    "0": (n["nivel_0"] if n else 0) or 0,
                },
            })

        # ── Resumen base por cuento ──────────────────────────────────────
        cuentos_data = conn.execute("""
            SELECT cuento_id, COUNT(DISTINCT fragmento) AS completados
            FROM progreso_cuento WHERE sender_id = ? GROUP BY cuento_id
        """, (sender_id,)).fetchall()

        # ── Desglose por nivel para cuentos ──────────────────────────────
        cuento_niveles = conn.execute("""
            SELECT
                cuento_id,
                SUM(CASE WHEN max_nivel=2 THEN 1 ELSE 0 END) AS nivel_2,
                SUM(CASE WHEN max_nivel=1 THEN 1 ELSE 0 END) AS nivel_1,
                SUM(CASE WHEN max_nivel=0 THEN 1 ELSE 0 END) AS nivel_0
            FROM (
                SELECT cuento_id, fragmento, MAX(nivel) AS max_nivel
                FROM progreso_cuento
                WHERE sender_id = ?
                GROUP BY cuento_id, fragmento
            )
            GROUP BY cuento_id
        """, (sender_id,)).fetchall()
        nivc = {r["cuento_id"]: r for r in cuento_niveles}

        cuentos = []
        for r in cuentos_data:
            cid = r["cuento_id"]
            n = nivc.get(cid)
            cuentos.append({
                "cuento_id":   cid,
                "completados": r["completados"],
                "niveles": {
                    "2": (n["nivel_2"] if n else 0) or 0,
                    "1": (n["nivel_1"] if n else 0) or 0,
                    "0": (n["nivel_0"] if n else 0) or 0,
                },
            })

        return {"categorias": categorias, "cuentos": cuentos}
    finally:
        conn.close()


def _cuentos_xlsx_path():
    for c in CUENTOS_XLSX_CANDIDATES:
        if c.exists():
            return c
    return None


def _leer_cuentos(sender_id: str):
    """
    Lee cuentos.xlsx + progreso del usuario en la DB y devuelve metadatos
    enriquecidos para la biblioteca de cuentos.
    """
    xlsx = _cuentos_xlsx_path()
    if not xlsx:
        return {"error": "cuentos.xlsx no encontrado", "cuentos": []}

    try:
        from openpyxl import load_workbook
    except ImportError:
        return {"error": "openpyxl no instalado", "cuentos": []}

    wb = load_workbook(str(xlsx), read_only=True, data_only=True)
    ws = wb["cuentos"] if "cuentos" in wb.sheetnames else wb.active

    # Agrupar fragmentos por cuento_id
    cuentos_raw = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        cuento_id     = str(row[0]).strip()
        cuento_titulo = str(row[1]).strip() if row[1] else cuento_id
        respuesta_esp = str(row[5]).strip() if len(row) > 5 and row[5] else ""

        if cuento_id not in cuentos_raw:
            cuentos_raw[cuento_id] = {
                "id":      cuento_id,
                "titulo":  cuento_titulo,
                "total":   0,
                "palabras": [],
            }
        cuentos_raw[cuento_id]["total"] += 1
        if respuesta_esp and respuesta_esp not in cuentos_raw[cuento_id]["palabras"]:
            cuentos_raw[cuento_id]["palabras"].append(respuesta_esp)
    wb.close()

    # Leer progreso por cuento desde la DB
    progreso = {}
    db = _db_path()
    if db:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT cuento_id, COUNT(DISTINCT fragmento) AS completados
                FROM progreso_cuento WHERE sender_id = ? GROUP BY cuento_id
            """, (sender_id,)).fetchall()
            progreso = {r["cuento_id"]: r["completados"] for r in rows}
        finally:
            conn.close()

    # Combinar metadatos
    resultado = []
    for cid, info in cuentos_raw.items():
        meta = CUENTO_META.get(cid, {"emoji": "📖", "descripcion": ""})
        completados = progreso.get(cid, 0)
        total = info["total"]
        resultado.append({
            "id":           cid,
            "titulo":       info["titulo"],
            "emoji":        meta["emoji"],
            "descripcion":  meta["descripcion"],
            "palabras":     info["palabras"],
            "total":        total,
            "completados":  min(completados, total),
            "porcentaje":   round(min(completados, total) / total * 100) if total else 0,
        })
    return {"cuentos": resultado}


class PishicoProxy(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                content = HTML_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self._error(404, "index.html no encontrado")

        elif self.path == "/status":
            try:
                with urllib.request.urlopen(RASA_STATUS, timeout=3) as r:
                    self._json(200, json.loads(r.read()))
            except Exception:
                self._json(503, {"error": "Rasa no disponible"})

        elif self.path.startswith("/images/"):
            name = self.path.split("/images/", 1)[1]
            path = (IMAGES_DIR / name).resolve()
            if not str(path).startswith(str(IMAGES_DIR.resolve())) or not path.is_file():
                self._error(404, "Imagen no encontrada")
                return
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(path.read_bytes())

        elif self.path.startswith("/progreso/"):
            sid = self.path.split("/progreso/", 1)[1]
            if not sid:
                self._json(400, {"error": "Falta sender_id"})
                return
            data = _leer_progreso(sid)
            self._json(200, data)

        elif self.path.startswith("/cuentos/"):
            sid = self.path.split("/cuentos/", 1)[1]
            if not sid:
                self._json(400, {"error": "Falta sender_id"})
                return
            data = _leer_cuentos(sid)
            self._json(200, data)

        else:
            self._error(404, "Ruta no encontrada")

    def do_POST(self):
        if "/webhooks/rest/webhook" not in self.path:
            self._error(404, "Ruta no encontrada")
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b""

        try:
            req = urllib.request.Request(
                RASA_WEBHOOK,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = resp.read()
            self._json_raw(200, result)
        except urllib.error.URLError:
            self._json(503, [{"text": (
                "⚠️ No puedo conectar con Rasa. Asegúrate de que esté corriendo."
            )}])
        except Exception as e:
            self._json(500, [{"text": f"Error interno: {e}"}])

    # ── Helpers ───────────────────────────────────────────────────────
    def _json(self, code, data):
        self._json_raw(code, json.dumps(data, ensure_ascii=False).encode())

    def _json_raw(self, code, raw):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, code, msg):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(msg.encode())

    def log_message(self, fmt, *args):
        if any(c in str(args) for c in ("500", "503")):
            print(f"[proxy] {self.address_string()} {args}")


def main():
    if not HTML_FILE.exists():
        print(f"⚠️  No se encontró {HTML_FILE}")
        return

    db = _db_path()
    print("=" * 52)
    print("  Pishico Bot — Servidor frontend")
    print("=" * 52)
    print(f"  Frontend : http://localhost:{PORT}")
    print(f"  Rasa API : {RASA_WEBHOOK}")
    print(f"  DB       : {db if db else '(no encontrada todavía)'}")
    print("=" * 52)

    server = HTTPServer(("localhost", PORT), PishicoProxy)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")


if __name__ == "__main__":
    main()
