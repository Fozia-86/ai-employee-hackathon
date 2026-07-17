import os
import argparse
from cryptography.fernet import Fernet
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = PROJECT_ROOT / ".vault_key"
if not KEY_FILE.exists():
    KEY_FILE.write_bytes(Fernet.generate_key())

cipher_suite = Fernet(KEY_FILE.read_bytes())

def encrypt_file(file_path):
    path = Path(file_path)
    if not path.exists(): return
    data = path.read_bytes()
    encrypted_data = cipher_suite.encrypt(data)
    path.write_bytes(encrypted_data)
    path.rename(path.with_suffix('.enc'))
    print(f"🔒 {path.name} encrypted.")

def rotate_api_key(service_name, new_key):
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        env_path.write_text("")
    lines = env_path.read_text().splitlines()
    updated = False
    with open(env_path, "w") as f:
        for line in lines:
            if line.startswith(f"{service_name}="):
                f.write(f"{service_name}={new_key}\n")
                updated = True
            else:
                f.write(line + "\n")
        if not updated:
            f.write(f"{service_name}={new_key}\n")
    print(f"🔄 {service_name} API Key rotated.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--encrypt", help="Path to file to encrypt")
    parser.add_argument("--rotate", nargs=2, help="Service name and new key")
    args = parser.parse_args()

    if args.encrypt: encrypt_file(args.encrypt)
    if args.rotate: rotate_api_key(args.rotate[0], args.rotate[1])
