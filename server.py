"""
server.py — Servidor proxy para el frontend de Pishico Bot.

Sirve index.html y reenvía las solicitudes al Action Server de Rasa
en el mismo proceso, eliminando el problema de CORS completamente.

Uso:
    python3 server.py

Luego abrir: http://localhost:8080
"""

import json
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

RASA_WEBHOOK = "http://localhost:5005/webhooks/rest/webhook"
RASA_STATUS  = "http://localhost:5005/status"
PORT         = 8080
HTML_FILE    = Path(__file__).parent / "index.html"


class PishicoProxy(BaseHTTPRequestHandler):

    # ── GET: servir index.html ────────────────────────────────────────
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                content = HTML_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self._error(404, "index.html no encontrado")

        elif self.path == "/status":
            # Health check: verifica si Rasa está corriendo
            try:
                with urllib.request.urlopen(RASA_STATUS, timeout=3) as r:
                    data = json.loads(r.read())
                self._json(200, data)
            except Exception:
                self._json(503, {"error": "Rasa no disponible"})
        else:
            self._error(404, "Ruta no encontrada")

    # ── POST: proxy a Rasa ────────────────────────────────────────────
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
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = resp.read()
            self._json_raw(200, result)

        except urllib.error.URLError:
            self._json(503, [{"text": (
                "⚠️ No puedo conectar con Rasa. "
                "Asegurate de que el servidor esté corriendo con: rasa run --enable-api"
            )}])
        except Exception as e:
            self._json(500, [{"text": f"Error interno: {e}"}])

    # ── Helpers ───────────────────────────────────────────────────────
    def _json(self, code, data):
        self._json_raw(code, json.dumps(data).encode())

    def _json_raw(self, code, raw):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, code, msg):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(msg.encode())

    def log_message(self, fmt, *args):
        # Silenciar logs repetitivos; solo mostrar errores
        if "500" in str(args) or "503" in str(args):
            print(f"[proxy] {self.address_string()} {args}")


def main():
    if not HTML_FILE.exists():
        print(f"⚠️  No se encontró {HTML_FILE}")
        print("   Asegurate de que index.html esté en la misma carpeta que server.py")
        return

    server = HTTPServer(("localhost", PORT), PishicoProxy)
    print("=" * 52)
    print("  Pishico Bot — Servidor frontend")
    print("=" * 52)
    print(f"  Frontend : http://localhost:{PORT}")
    print(f"  Rasa API : {RASA_WEBHOOK}")
    print(f"  HTML     : {HTML_FILE}")
    print("=" * 52)
    print("  Abrí http://localhost:8080 en tu navegador")
    print("  Ctrl+C para detener")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")


if __name__ == "__main__":
    main()
