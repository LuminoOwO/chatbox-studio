"""
End-to-end test of the Vulkan speech engine with Chatbox Studio's client (whispercpp.py):
starts the real whisper-server, transcribes a sample and checks the answer.

    ENGINE=<whisper-server> MODEL=<ggml model> AUDIO=<16 kHz mono wav> python3 tests/test_whispercpp_engine.py

Run by .github/workflows/engines.yml (software Vulkan driver, tiny model).
"""

import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
home = tempfile.mkdtemp(prefix="cbs-test-")
os.environ["HOME"] = home
os.environ.pop("XDG_DATA_HOME", None)

import paths  # noqa: E402
import whispercpp as W  # noqa: E402

eng = W.engine_dir()
eng.mkdir(parents=True, exist_ok=True)
shutil.copy2(os.environ["ENGINE"], W.server_exe())
W.server_exe().chmod(0o755)
W.MODELS["small"] = (Path(os.environ["MODEL"]).name, 75, 200)
W.model_file("small").parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(os.environ["MODEL"], W.model_file("small"))

with wave.open(os.environ["AUDIO"]) as w:
    assert w.getframerate() == 16000 and w.getnchannels() == 1
    audio = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype("float32") / 32768

logs = []
t = W.Transcriber("small", "vulkan:0", logs.append)
print("\n".join(logs))
r = t.run(audio, ["en", "es"], 1, {"en": (False, True), "es": (True, True)})
print("language:", r.language, round(r.lang_prob, 2), "text:", r.text, "dropped:", r.dropped)
room = t.run_room(audio, True)
print("room:", room)
t.close()
assert r.language == "en", r.language
assert "country" in r.text.lower(), r.text
assert room and room[0] == "en"
print("OK")
