import sys
import os
import time
import signal
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("falkor_worker")

# Global to handle shutdown
db_instance = None

def handle_signal(signum, frame):
    logger.info(f"Received signal {signum}. Stopping FalkorDB worker...")
    sys.exit(0)

def run_worker():
    global db_instance
    
    # Get configuration from env
    db_path = os.getenv('FALKORDB_PATH')
    socket_path = os.getenv('FALKORDB_SOCKET_PATH')
    
    if not db_path or not socket_path:
        logger.error("Missing configuration. FALKORDB_PATH and FALKORDB_SOCKET_PATH must be set.")
        sys.exit(1)
        
    # Ensure dir exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Starting FalkorDB Lite worker...")
    logger.info(f"DB Path: {db_path}")
    logger.info(f"Socket: {socket_path}")
    
    try:
        import platform
        
        if platform.system() == "Windows":
            raise RuntimeError(
                "CodeGraphContext uses redislite/FalkorDB, which does not support Windows.\n"
                "Please run the project using WSL or Docker."
            )
        
        from redislite.falkordb_client import FalkorDB
        
        # Determine module path for frozen bundles
        server_config = {}
        if getattr(sys, 'frozen', False):
            mei_pass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
            
            # Potential locations in the bundle
            potential_paths = [
                os.path.join(mei_pass, 'redislite', 'bin', 'falkordb.so'),
                os.path.join(mei_pass, 'falkordblite.scripts', 'falkordb.so'),
                os.path.join(mei_pass, 'falkordb.so'),
            ]
            
            module_path = None
            for p in potential_paths:
                if os.path.exists(p):
                    module_path = p
                    break
            
            if module_path:
                logger.info(f"Using FalkorDB module from bundle: {module_path}")
                server_config['loadmodule'] = module_path
            else:
                logger.warning("Could not find falkordb.so in bundle. Falling back to automatic discovery.")

        # Start Embedded DB
        if os.path.exists(socket_path):
            try:
                os.remove(socket_path)
            except OSError:
                pass

        db_instance = FalkorDB(db_path, unix_socket_path=socket_path, serverconfig=server_config)
        logger.info("FalkorDB Lite is running.")
        
        # Keep alive loop
        while True:
            time.sleep(1)
            
    except ImportError as e:
        logger.error(f"Failed to import redislite.falkordb_client: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        logger.error(f"FalkorDB Worker Critical Failure: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    run_worker()
