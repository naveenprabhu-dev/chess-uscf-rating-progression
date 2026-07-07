import os

from flask import Flask, flash
from werkzeug.middleware.proxy_fix import ProxyFix

from .cache import close_db, init_db


def _is_production():
    """Production is opt-in via APP_ENV=production. It controls fail-closed secret
    handling and HTTPS-only cookies. (Flask removed FLASK_ENV in 2.3, so we use
    our own flag rather than relying on a framework one.)"""
    return os.environ.get("APP_ENV", "").lower() == "production"


def create_app():
    app = Flask(__name__, instance_relative_config=True)

    # The SECRET_KEY signs the session cookie (which holds the user's library).
    # A known default is forgeable, so in production a real secret is REQUIRED —
    # fail loudly at boot rather than silently shipping a guessable key.
    secret = os.environ.get("FLASK_SECRET_KEY")
    if not secret:
        if _is_production():
            raise RuntimeError(
                "FLASK_SECRET_KEY must be set when APP_ENV=production "
                "(it signs the session cookie; a known default is forgeable)."
            )
        secret = "dev-only-not-secure"
    app.config["SECRET_KEY"] = secret

    # Cookie hardening. HttpOnly is Flask's default — made explicit here. Secure
    # is only meaningful over HTTPS, so it's on in production and off for local
    # HTTP dev (otherwise the browser drops the cookie on http://localhost).
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_is_production(),
    )

    # In production we sit behind one reverse proxy (nginx / the PaaS router)
    # that terminates TLS. Trust its X-Forwarded-* headers so request.is_secure,
    # url_for's scheme, and the client IP are correct (the last also matters for
    # any future per-IP scrape throttle).
    if _is_production():
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    os.makedirs(app.instance_path, exist_ok=True)
    init_db(app)
    app.teardown_appcontext(close_db)

    # Cache-bust the stylesheet: ?v=<styles.css mtime>, computed once at boot.
    # Every deploy rewrites the file (fresh mtime), so browsers re-fetch the CSS
    # exactly when it changes instead of serving a stale cached copy — without
    # this, a redeployed page can render with the previous deploy's styles.
    css = os.path.join(app.static_folder, "styles.css")
    css_version = int(os.path.getmtime(css)) if os.path.exists(css) else 0

    @app.context_processor
    def _asset_version():
        return {"css_version": css_version}

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
