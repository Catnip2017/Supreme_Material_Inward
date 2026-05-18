import socket
from waitress import serve
from app import app
from config.logger import get_logger

logger = get_logger(__name__)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"

if __name__ == "__main__":
    host = "0.0.0.0"
    port = 5003
    local_ip = get_local_ip()
    logger.info(f"Starting production server on {host}:{port}")
    print(f"Server running on http://{host}:{port}")
    print(f"Access from network: http://{local_ip}:{port}")
    serve(
        app,
        host=host,
        port=port,
        threads=8,
        connection_limit=100,
        cleanup_interval=30,
        channel_timeout=120
    )