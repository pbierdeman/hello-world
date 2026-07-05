import os

from flask import Flask
from .pubchem import init_db
from . import formula_store


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SDS_SECRET_KEY', 'change-this-in-production')
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    init_db()

    # App-writable formula library (separate from the read-only master DB)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config['FORMULA_DB'] = os.path.join(app.instance_path, 'formulas.db')
    formula_store.DEFAULT_DB_PATH = app.config['FORMULA_DB']
    formula_store.init_schema(app.config['FORMULA_DB'])

    from .routes import main
    app.register_blueprint(main)

    return app
