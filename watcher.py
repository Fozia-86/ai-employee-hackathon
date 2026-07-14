import time
import shutil
import logging
from pathlib import Path
from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler

# Configuration
VAULT_PATH = Path("./").absolute()
INBOX_PATH = VAULT_PATH / "Inbox"
NEEDS_ACTION_PATH = VAULT_PATH / "Needs_Action"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class InboxHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        if file_path.suffix == '.tmp': # Ignore temp files
            return

        logging.info(f"New file detected: {file_path.name}")
        time.sleep(0.5) # Wait for file write to complete
        
        try:
            # 1. Move file to Needs_Action
            destination = NEEDS_ACTION_PATH / file_path.name
            shutil.move(str(file_path), str(destination))
            logging.info(f"Moved {file_path.name} to Needs_Action")
            
            # 2. Create a Metadata Trigger file for Claude
            meta_filename = f"TRIGGER_{file_path.stem}.md"
            with open(NEEDS_ACTION_PATH / meta_filename, "w") as f:
                f.write(f"---\ntype: triage_trigger\noriginal_file: {file_path.name}\ndetected_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\nstatus: pending\n---\n\n"
                        f"Claude, a new file `{file_path.name}` has arrived in the Inbox. "
                        f"Please analyze this file, update the Dashboard, and create a plan.")
            
            logging.info(f"Triage trigger created for {file_path.name}")
        except Exception as e:
            logging.error(f"Error processing file: {e}")

if __name__ == "__main__":
    # Ensure folders exist
    INBOX_PATH.mkdir(exist_ok=True)
    NEEDS_ACTION_PATH.mkdir(exist_ok=True)
    
    event_handler = InboxHandler()
    observer = Observer() # Using PollingObserver for WSL/Windows compatibility
    observer.schedule(event_handler, str(INBOX_PATH), recursive=False)
    
    logging.info(f"Polling Watcher started on {INBOX_PATH}")
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
