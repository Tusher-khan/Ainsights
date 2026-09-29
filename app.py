"""AInsight — AI-Powered Business Analytics.

Entry point. Run with:

    python app.py

or:

    flask --app app run --port 5000
"""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from config import Config
from app.database.connection import init_store
from app.utils.exceptions import AInsightError
from app.utils.helpers import new_id
from app.utils.logger import configure_logging, get_logger

log = get_logger("app")


def create_app(config: type[Config] = Config) -> Flask:
    configure_logging()
    config.ensure_dirs()

    application = Flask(__name__, template_folder="templates", static_folder="static")
    application.config.from_object(config)
    application.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
    application.config["JSON_SORT_KEYS"] = False

    init_store()

    # Register API blueprints.
    from app.routes.analytics_routes import analytics_bp
    from app.routes.chat_routes import chat_bp
    from app.routes.dataset_routes import dataset_bp
    from app.routes.system_routes import system_bp
    from app.routes.upload_routes import upload_bp

    for blueprint in (chat_bp, upload_bp, dataset_bp, analytics_bp, system_bp):
        application.register_blueprint(blueprint)

    # --- pages ----------------------------------------------------------
    @application.route("/")
    def landing():
        return render_template("index.html")

    @application.route("/dashboard")
    def dashboard():
        return render_template("dashboard.html", active="dashboard")

    @application.route("/chat")
    def chat():
        return render_template("chat.html", active="chat")

    @application.route("/datasets")
    def datasets():
        return render_template("datasets.html", active="datasets")

    @application.route("/datasets/<dataset_id>")
    def dataset_profile(dataset_id: str):
        return render_template("dataset_profile.html", active="datasets", dataset_id=dataset_id)

    @application.route("/upload")
    def upload():
        return render_template("upload.html", active="datasets")

    @application.route("/history")
    def history():
        return render_template("history.html", active="history")

    @application.route("/evaluation")
    def evaluation():
        return render_template("evaluation.html", active="evaluation")

    @application.route("/system")
    def system():
        return render_template("system.html", active="system")

    # --- request context --------------------------------------------------
    @application.before_request
    def attach_request_id():
        request.environ["ainsight.request_id"] = new_id("req")

    # --- error handling ---------------------------------------------------
    @application.errorhandler(AInsightError)
    def handle_known_error(error: AInsightError):
        log.warning(
            "[%s] %s: %s",
            request.environ.get("ainsight.request_id", "-"),
            type(error).__name__,
            error.detail,
        )
        return jsonify(error.to_dict()), error.status_code

    @application.errorhandler(404)
    def handle_not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "NotFound", "message": "That endpoint does not exist."}), 404
        return render_template("index.html"), 404

    @application.errorhandler(413)
    def handle_too_large(_error):
        return (
            jsonify(
                {
                    "error": "FileTooLarge",
                    "message": f"Files must be under {config.MAX_FILE_SIZE_MB} MB.",
                }
            ),
            413,
        )

    @application.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        # Stack traces are logged, never returned to the browser.
        log.exception(
            "[%s] Unhandled error: %s", request.environ.get("ainsight.request_id", "-"), error
        )
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    {
                        "error": "InternalError",
                        "message": "Something went wrong on our side. The details were logged.",
                    }
                ),
                500,
            )
        return render_template("index.html"), 500

    log.info(
        "AInsight ready | provider=%s | demo_mode=%s | debug=%s",
        config.LLM_PROVIDER,
        config.DEMO_MODE,
        config.DEBUG,
    )
    return application


app = create_app()


if __name__ == "__main__":
    # Load any example/user files sitting in data/ so the app is useful immediately.
    try:
        from app.services.file_service import autoload_examples

        autoload_examples()
    except Exception as exc:  # startup must not fail on a bad example file
        log.warning("Example autoload skipped: %s", exc)

    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
