"""`python3 -m studio` — launch the Firefly Studio server on localhost and open
the browser. Localhost-only by design (single local user, no auth; see the spec)."""
import argparse
import threading
import webbrowser

import uvicorn

from studio.server.app import create_app


def main() -> None:
    ap = argparse.ArgumentParser(prog="studio")
    ap.add_argument("--port", type=int, default=8676)
    ap.add_argument("--stock", default="firefly-13.6/mcf.bin")
    ap.add_argument("--projects", default="projects")
    ap.add_argument("--build-root", default="build")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    app = create_app(stock_path=args.stock, projects_root=args.projects,
                     build_root=args.build_root)
    url = "http://127.0.0.1:%d/api/docs" % args.port
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print("Firefly Studio: %s" % url)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
