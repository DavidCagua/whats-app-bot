import logging
import os

from app import create_app


app = create_app()

if __name__ == "__main__":
    # Set debug mode based on environment
    debug_mode = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
    
    if debug_mode:
        logging.info("Flask app started in DEBUG mode")
    else:
        logging.info("Flask app started in PRODUCTION mode")
    
    app.run(host="0.0.0.0", port=8000, debug=debug_mode)
