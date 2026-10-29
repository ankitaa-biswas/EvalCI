"""Container checks: python -m core.health worker | chroma."""
import argparse
import os
import socket


def worker_ready():
    from core.queue import celery_app
    node = "worker@" + socket.gethostname()
    replies = celery_app.control.inspect(destination=[node], timeout=3).ping()
    if not replies or replies.get(node, {}).get("ok") != "pong":
        raise RuntimeError("Celery worker did not answer ping")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=["worker", "chroma"])
    args = parser.parse_args()
    if args.service == "worker":
        worker_ready()
    else:
        import urllib.request
        urllib.request.urlopen("http://localhost:8000/api/v1/heartbeat", timeout=3).close()


if __name__ == "__main__":
    main()
