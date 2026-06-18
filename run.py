import logging
import os

from webapp import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = create_app()

if __name__ == "__main__":
    # Local dev convenience only. Production runs under gunicorn (see Procfile /
    # gunicorn.conf.py), which imports `app` above and never reaches this block.
    # debug defaults OFF (the Werkzeug debugger is RCE if exposed); opt in with
    # FLASK_DEBUG=1 for local work.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes", "on")
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5050")),
        debug=debug,
    )
