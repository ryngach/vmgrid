from gevent import monkey
monkey.patch_all()

import logging
from flask import Flask
from graphql_server.flask import GraphQLView
from gevent.pywsgi import WSGIServer
from schema import schema
from config import AppConfig
from flask import request, jsonify
from hypervisor_implementations import HYPERVISOR_TYPES
import gevent
import signal
import sys

# Configure logging first, with basic configuration
logging.basicConfig(
    level="INFO",
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("main")

# Load config
config = AppConfig.load()

# Update logging configuration with values from config
root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, config.logging.level.upper()))
# Remove all handlers and add new one with correct format
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(config.logging.format))
root_logger.addHandler(handler)

# Initialize hypervisor
hypervisor_class = HYPERVISOR_TYPES.get(config.hypervisor.type)
if not hypervisor_class:
    raise ValueError(f"Unknown hypervisor type: {config.hypervisor.type}")
hypervisor = hypervisor_class()

# Initialize Flask app
app = Flask(__name__)
app.debug = config.server.debug

# Register GraphQL view
app.add_url_rule(
    config.graphql.path,
    view_func=GraphQLView.as_view(
        'graphql',
        schema=schema,
        graphiql=config.graphql.graphiql,
        batch=config.graphql.batch
    )
)

@app.errorhandler(Exception)
def handle_error(error):
    logger.error("Unhandled error: %s", str(error), exc_info=True)
    return jsonify({"error": str(error)}), 500

@app.route('/health')
def health_check():
    return {'status': 'healthy'}, 200

def shutdown_handler():
    logger.info('Shutting down gracefully...')
    hypervisor.stop_processing()
    http_server.stop()
    logger.info('Server has been stopped.')
    sys.exit(0)

if __name__ == '__main__':
    logger.info('Starting server on %s:%s', config.server.host, config.server.port)
    
    # Start hypervisor task processing in background
    gevent.spawn(hypervisor.start_processing)
    
    http_server = WSGIServer(
        (config.server.host, config.server.port), 
        app,
        log=logger
    )

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    http_server.serve_forever()