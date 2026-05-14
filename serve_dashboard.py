#!/usr/bin/env python3
import http.server
import socketserver
import os
import sys

PORT = 2081
DIRECTORY = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # Disable caching to ensure frontend fetches latest JSONs
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        return super().end_headers()


def main():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving dashboard at http://localhost:{PORT}/frontend/index.html")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server")
            sys.exit(0)


if __name__ == "__main__":
    main()
