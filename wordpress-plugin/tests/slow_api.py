"""A pretend outside service that takes 300 ms to answer (Slowpoke calls it on every page)."""
import http.server
import time


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.3)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": 1}')

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", 8897), Handler).serve_forever()
