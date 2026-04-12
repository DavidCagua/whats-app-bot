from flask import Flask
from app.config import load_configurations, configure_logging


def create_app():
    # Import views inside the factory so `import app.database.models` (or
    # any other submodule) doesn't drag in the Flask routing layer and
    # transitively the agent registry + LLM clients. This keeps Alembic,
    # unit tests and scripts cheap to boot without needing env vars
    # required only by the runtime bot.
    from .views import webhook_blueprint

    app = Flask(__name__)

    load_configurations(app)
    configure_logging()

    app.register_blueprint(webhook_blueprint)

    return app
