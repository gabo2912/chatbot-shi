"""
server.py — Servidor proxy para el frontend de Pishico Bot.

Endpoints:
  GET  /                          → sirve index.html
  GET  /status                    → estado de Rasa
  GET  /progreso/<sender_id>      → progreso del usuario desde la DB (JSON)
  POST /webhooks/rest/webhook     → proxy a Rasa

Uso:
    python3 server.py
"""

import json
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
DB_CANDIDATES = [
    HERE / "actions" / "progress.db",
    HERE / "progress.db",
    HERE.parent / "actions" / "progress.db",
]

TOTAL_PALABRAS = {
    "naturaleza": 11, "animales": 13, "cuerpo": 12, "colores": 9, "objetos": 9
}
EMOJI_CAT = {
    "naturaleza": "🌿", "animales": "🦜", "cuerpo": "🫀", "colores": "🎨", "objetos": "🏺"
}


def _db_path():
    for c in DB_CANDIDATES:
        if c.exists():
            return c
    return None


def _leer_progreso(sender_id: str):
    """Lee la DB y devuelve dict con categorías + cuentos."""
    db = _db_path()
    if not db:
        return {"error": "DB no encontrada", "categorias": [], "cuentos": []}

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        # Categorías
        cats_data = conn.execute("""
            SELECT
                categoria,
                COUNT(DISTINCT CASE WHEN resultado='correcto' THEN palabra_es END) AS dominadas,
                COUNT(DISTINCT palabra_es) AS vistas
            FROM progreso_vocabulario
            WHERE sender_id = ?
            GROUP BY categoria
        """, (sender_id,)).fetchall()

        visto = {r["categoria"]: r for r in cats_data}
        categorias = []
        for cat in ["naturaleza", "animales", "cuerpo", "colores", "objetos"]:
            r = visto.get(cat)
            dom = r["dominadas"] if r else 0
            total = TOTAL_PALABRAS.get(cat, 0)
            categorias.append({
                "categoria":  cat,
                "emoji":      EMOJI_CAT.get(cat, "📚"),
                "dominadas":  dom,
                "total":      total,
                "porcentaje": round(dom / total * 100) if total else 0,
            })

        # Cuentos
        cuentos_data = conn.execute("""
            SELECT cuento_id, COUNT(DISTINCT fragmento) AS completados
            FROM progreso_cuento WHERE sender_id = ? GROUP BY cuento_id
        """, (sender_id,)).fetchall()
        cuentos = [dict(r) for r in cuentos_data]

        return {"categorias": categorias, "cuentos": cuentos}
    finally:
        conn.close()


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

        elif self.path.startswith("/progreso/"):
            sid = self.path.split("/progreso/", 1)[1]
            if not sid:
                self._json(400, {"error": "Falta sender_id"})
                return
            data = _leer_progreso(sid)
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