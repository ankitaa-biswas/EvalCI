"""Create a private local Compose env file without overwriting an existing file."""
import argparse
import os
from pathlib import Path
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path",nargs="?",type=Path,default=Path(".env.local"))
    args = parser.parse_args()
    contents = (f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n"
                f"EVALCI_API_KEYS={secrets.token_hex(24)}\nAPI_PORT=8080\nRAG_PORT=8001\n")
    fd = os.open(args.path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd,"w") as output: output.write(contents)
    print(f"Created {args.path}. Fixture mode needs no provider keys; configure live settings separately.")


if __name__ == "__main__": main()
