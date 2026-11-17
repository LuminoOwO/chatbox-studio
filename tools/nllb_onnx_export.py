"""Makes the DirectML (ONNX) version of an NLLB translator and checks it.

Used by .github/workflows/nllb-onnx.yml:

    python tools/nllb_onnx_export.py fp16 <optimum export dir> <out dir>
    python tools/nllb_onnx_export.py check <model name> <out dir>

fp16: encoder_model.onnx + decoder_model_merged.onnx with float16 weights and float32
inputs / outputs (one file each, no external data), plus config.json.
check: translates sample sentences with src/nllb_onnx.py (CPU) and with the CTranslate2
model the app uses, prints both and fails when they disagree too often.
"""

import json
import shutil
import sys
from pathlib import Path


def fp16(src: Path, out: Path):
    import onnx
    from onnxconverter_common import float16
    out.mkdir(parents=True, exist_ok=True)
    for name in ("encoder_model.onnx", "decoder_model_merged.onnx"):
        m = onnx.load(str(src / name), load_external_data=True)
        m = float16.convert_float_to_float16(m, keep_io_types=True, disable_shape_infer=True)
        onnx.save(m, str(out / name))
        print(f"{name}: {(out / name).stat().st_size / 1e6:.0f} MB")
        report(m)
    cfg = json.loads((src / "config.json").read_text())
    keep = {k: cfg[k] for k in ("d_model", "decoder_layers", "decoder_attention_heads",
                                "encoder_layers", "encoder_attention_heads", "eos_token_id",
                                "decoder_start_token_id", "pad_token_id", "vocab_size") if k in cfg}
    (out / "config.json").write_text(json.dumps(keep, indent=2) + "\n")


def report(m):
    """Bytes of weights per data type, also inside subgraphs (If branches)."""
    import onnx
    from onnx import numpy_helper
    sizes: dict = {}

    def walk(g):
        for t in g.initializer:
            k = onnx.TensorProto.DataType.Name(t.data_type)
            sizes[k] = sizes.get(k, 0) + numpy_helper.to_array(t).nbytes
        for n in g.node:
            for a in n.attribute:
                if a.type == onnx.AttributeProto.GRAPH:
                    walk(a.g)
    walk(m.graph)
    print("   weights:", {k: f"{v / 1e6:.0f} MB" for k, v in sizes.items()})


SAMPLES = [
    ("en", "de", "Hello! How are you doing today?"),
    ("en", "es", "I think we should go to the other world, the music there is great."),
    ("en", "ja", "Can you hear me? My microphone was muted."),
    ("en", "fr", "Thank you so much for the help earlier."),
    ("de", "en", "Ich habe keine Ahnung, wo der Ausgang ist."),
    ("es", "en", "¿Alguien quiere jugar a las cartas conmigo?"),
    ("en", "ko", "Good night everyone, see you tomorrow."),
    ("en", "pt", "This avatar looks really cool, where did you get it?"),
]


def check(name: str, out: Path) -> int:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "src"))
    import translator
    import nllb_onnx
    ct2_dir = translator.model_path(name)
    if not translator.is_installed(name):
        translator.download(name, lambda p: None)
    dest = nllb_onnx.onnx_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    for f in ("encoder_model.onnx", "decoder_model_merged.onnx", "config.json"):
        shutil.copy(out / f, dest / f)
    ref = translator.TextTranslator(name, "cpu", print)
    onnx_tr = nllb_onnx.OnnxTranslator(name, "cpu", print, providers=["CPUExecutionProvider"])
    same = 0
    for src, dst, text in SAMPLES:
        a = ref.translate(text, src, dst)
        b = onnx_tr.translate(text, src, dst)
        same += a == b
        print(f"[{src}->{dst}] {text}\n   ct2 : {a}\n   onnx: {b}")
        if not b.strip():
            print("EMPTY translation")
            return 1
    print(f"{same}/{len(SAMPLES)} identical")
    # int8 (CTranslate2) and fp16 (ONNX) differ a little; most must still match exactly
    return 0 if same >= len(SAMPLES) // 2 else 1


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fp16":
        fp16(Path(sys.argv[2]), Path(sys.argv[3]))
    elif cmd == "check":
        sys.exit(check(sys.argv[2], Path(sys.argv[3])))
