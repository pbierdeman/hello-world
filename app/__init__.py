from flask import Flask
from .pubchem import init_db


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'change-this-in-production'
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    init_db()

    from .routes import main
    app.register_blueprint(main)

    return app
