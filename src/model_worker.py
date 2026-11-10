"""
Helper process that runs AI models with another build of CTranslate2 (the AMD / ROCm
one). Started by workers.py; talks over stdin/stdout (length-prefixed pickle), and
writes its log to stderr.

    model_worker.py rocm
"""

from __future__ import annotations

import os
import pickle
import struct
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
backend = sys.argv[1] if len(sys.argv) > 1 else "rocm"
sys.path.insert(0, str(HERE))

import paths  # noqa: E402
import devices  # noqa: E402

if backend == "rocm":
    sys.path.insert(0, str(devices.rocm_engine_dir()))   # the ROCm build of CTranslate2

out = sys.stdout.buffer
sys.stdout = sys.stderr                  # print() and libraries log to stderr, not the pipe
inp = sys.stdin.buffer


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def reply(rid, ok, value):
    data = pickle.dumps((rid, ok, value), protocol=pickle.HIGHEST_PROTOCOL)
    out.write(struct.pack("<I", len(data)) + data)
    out.flush()


def main():
    import workers
    objects = {}
    next_id = 0
    while True:
        head = inp.read(4)
        if len(head) < 4:
            return
        (n,) = struct.unpack("<I", head)
        rid, op, payload = pickle.loads(inp.read(n))
        try:
            if op == "load":
                kind, spec, args = payload
                obj = workers.build_local(kind, spec, *args, log=log)
                next_id += 1
                objects[next_id] = obj
                attrs = {k: v for k, v in vars(obj).items()
                         if isinstance(v, (str, int, float, bool)) and not k.startswith("_")}
                reply(rid, True, (next_id, attrs))
            elif op == "call":
                obj_id, method, args, kwargs = payload
                reply(rid, True, getattr(objects[obj_id], method)(*args, **kwargs))
            elif op == "free":
                objects.pop(payload[0], None)
                reply(rid, True, None)
            elif op == "ping":
                import ctranslate2
                reply(rid, True, {"devices": ctranslate2.get_cuda_device_count(),
                                  "file": ctranslate2.__file__})
            else:
                reply(rid, False, f"unknown op {op}")
        except Exception as e:
            log(traceback.format_exc().rstrip())
            reply(rid, False, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    os.environ.setdefault("CT2_CUDA_ALLOCATOR", "cub_caching")
    try:
        main()
    except KeyboardInterrupt:
        pass
