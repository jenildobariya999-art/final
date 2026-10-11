import argparse
from .server import serve

p = argparse.ArgumentParser(prog="selftbc")
p.add_argument("--host", default="0.0.0.0")
p.add_argument("--port", type=int, default=None)
a = p.parse_args()
serve(a.host, a.port)
