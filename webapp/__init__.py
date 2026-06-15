import os

from flask import Flask, flash

from .cache import close_db, init_db


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-only-not-secure")

    os.makedirs(app.instance_path, exist_ok=True)
    init_db(app)
    app.teardown_appcontext(close_db)

    @app.before_request
    def _flash_cache_reset():
        # init_db sets CACHE_WAS_RESET when it drops a legacy (uscf_id-keyed)
        # table. Surface a one-time notice, then clear the flag.
        if app.config.get("CACHE_WAS_RESET"):
            app.config["CACHE_WAS_RESET"] = False
            flash("Cache was reset to support the new FIDE source.", "info")

    from .routes import bp
    app.register_blueprint(bp)

    return app
