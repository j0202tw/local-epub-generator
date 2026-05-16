"""Run the EPUB Generator Flask app from the epub_generator directory."""
import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_app_dir = os.path.join(_script_dir, "epub_generator")
os.chdir(_app_dir)
sys.path.insert(0, _app_dir)

from app import app as application, cleanup_old_temp, _load_tasks, logger

if __name__ == "__main__":
    cleanup_old_temp()
    _load_tasks()
    logger.info("Starting EPUB Generator on http://127.0.0.1:5000")
    application.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
