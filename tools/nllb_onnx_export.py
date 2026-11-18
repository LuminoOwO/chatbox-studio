"""Makes the DirectML (ONNX) version of an NLLB translator and checks it.

Used by .github/workflows/nllb-onnx.yml:

    python tools/nllb_onnx_export.py export <model name> <out dir>
    python tools/nllb_onnx_export.py check <model name> <out dir>

export: two graphs without control flow, fp16 weights, float32 inputs / outputs:
  encoder_model.onnx         input_ids, attention_mask -> cross.<i>.key / .value for every
                             decoder layer (the encoder output already projected)
  decoder_model_merged.onnx  input_ids, encoder_attention_mask, past.<i>.key / .value (the
                             decoder's own cache, length 0 at the start), cross.<i>.* ->
                             logits of the last position, present.<i>.key / .value
plus config.json.
check: translates sample sentences with src/nllb_onnx.py (CPU) and with the CTranslate2
model the app uses, prints both and fails when they disagree too often.
"""

import json
import shutil
import sys
from pathlib import Path


def export(name: str, out: Path):
    import onnx
    import torch
    from onnxconverter_common import float16
    from transformers import AutoModelForSeq2SeqLM

    model = AutoModelForSeq2SeqLM.from_pretrained(f"facebook/{name}", torch_dtype=torch.float32)
    model.eval()
    cfg = model.config
    L, H = cfg.decoder_layers, cfg.decoder_attention_heads
    D = cfg.d_model // H

    class Enc(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            h = self.m.model.encoder(input_ids=input_ids,
                                     attention_mask=attention_mask).last_hidden_state
            b, n, _ = h.shape
            out = []
            for layer in self.m.model.decoder.layers:
                a = layer.encoder_attn
                out.append(a.k_proj(h).view(b, n, H, D).transpose(1, 2))
                out.append(a.v_proj(h).view(b, n, H, D).transpose(1, 2))
            return tuple(out)

    class Dec(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, encoder_attention_mask, *kv):
            past = tuple((kv[2 * i], kv[2 * i + 1], kv[2 * L + 2 * i], kv[2 * L + 2 * i + 1])
                         for i in range(L))
            # only its length is used: the cross-attention keys / values come from `past`
            dummy = encoder_attention_mask.unsqueeze(-1).to(torch.float32)
            o = self.m.model.decoder(input_ids=input_ids, encoder_hidden_states=dummy,
                                     encoder_attention_mask=encoder_attention_mask,
                                     past_key_values=past, use_cache=True, return_dict=True)
            # F.linear on the shared embedding exports as Gemm(transB=1) on the same weight;
            # lm_head(...) would get a transposed copy of it folded in (+525 MB)
            logits = torch.nn.functional.linear(o.last_hidden_state[:, -1, :],
                                                self.m.model.shared.weight)
            pres = []
            for layer in o.past_key_values:
                pres += [layer[0], layer[1]]
            return (logits, *pres)

    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "fp32"
    tmp.mkdir(exist_ok=True)
    b, n, p = 2, 7, 3
    ids = torch.randint(5, 1000, (b, n))
    mask = torch.ones(b, n, dtype=torch.int64)
    cross = [f"cross.{i}.{kv}" for i in range(L) for kv in ("key", "value")]
    past_names = [f"past.{i}.{kv}" for i in range(L) for kv in ("key", "value")]
    pres_names = [f"present.{i}.{kv}" for i in range(L) for kv in ("key", "value")]
    with torch.no_grad():
        torch.onnx.export(
            Enc(model), (ids, mask), str(tmp / "encoder_model.onnx"),
            input_names=["input_ids", "attention_mask"], output_names=cross,
            dynamic_axes={"input_ids": {0: "b", 1: "s"}, "attention_mask": {0: "b", 1: "s"},
                          **{c: {0: "b", 2: "s"} for c in cross}},
            opset_version=17, do_constant_folding=True)
        enc_kv = Enc(model)(ids, mask)
        dec_ids = torch.randint(5, 1000, (b, 1))
        past = [torch.randn(b, H, p, D) for _ in range(2 * L)]
        torch.onnx.export(
            Dec(model), (dec_ids, mask, *past, *enc_kv), str(tmp / "decoder_model_merged.onnx"),
            input_names=["input_ids", "encoder_attention_mask"] + past_names + cross,
            output_names=["logits"] + pres_names,
            dynamic_axes={"input_ids": {0: "b", 1: "t"}, "encoder_attention_mask": {0: "b", 1: "s"},
                          **{x: {0: "b", 2: "p"} for x in past_names},
                          **{c: {0: "b", 2: "s"} for c in cross},
                          "logits": {0: "b"}, **{x: {0: "b", 2: "p1"} for x in pres_names}},
            opset_version=17, do_constant_folding=True)
    del model
    for fname in ("encoder_model.onnx", "decoder_model_merged.onnx"):
        m = onnx.load(str(tmp / fname), load_external_data=True)
        if fname.startswith("decoder"):
            share_output_weights(m, cfg.vocab_size, cfg.d_model)
        m = float16.convert_float_to_float16(m, keep_io_types=True, disable_shape_infer=True)
        onnx.save(m, str(out / fname))
        print(f"{fname}: {(out / fname).stat().st_size / 1e6:.0f} MB")
        report(m)
    shutil.rmtree(tmp, ignore_errors=True)
    keep = {k: getattr(cfg, k) for k in ("d_model", "decoder_layers", "decoder_attention_heads",
                                         "encoder_layers", "encoder_attention_heads",
                                         "eos_token_id", "decoder_start_token_id",
                                         "pad_token_id", "vocab_size")}
    keep["format"] = 2
    (out / "config.json").write_text(json.dumps(keep, indent=2) + "\n")


def share_output_weights(m, vocab: int, d: int):
    """The exporter turns the output layer into MatMul(x, <transposed copy of the word
    embedding>). Point it at the embedding itself (Gemm with transB) and drop the copy."""
    import numpy as np
    from onnx import helper, numpy_helper
    inits = {t.name: t for t in m.graph.initializer}
    emb = next((t for t in m.graph.initializer if list(t.dims) == [vocab, d]), None)
    for node in m.graph.node:
        if node.op_type != "MatMul" or node.input[1] not in inits:
            continue
        w = inits[node.input[1]]
        if list(w.dims) != [d, vocab] or emb is None:
            continue
        a, b = numpy_helper.to_array(w), numpy_helper.to_array(emb)
        if not np.array_equal(a[:, :64], b[:64, :].T):
            raise SystemExit("the output layer isn't the transposed word embedding")
        gemm = helper.make_node("Gemm", [node.input[0], emb.name], list(node.output),
                                name=node.name + "_shared", transB=1)
        idx = list(m.graph.node).index(node)
        m.graph.node.remove(node)
        m.graph.node.insert(idx, gemm)
        m.graph.initializer.remove(w)
        print(f"   output layer shares the word embedding ({a.nbytes / 1e6:.0f} MB saved)")
        return
    print("   (no transposed embedding copy found)")


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
    # one model at a time: the runner has 16 GB, and the CPU runs fp16 as float32. The big
    # model only gets a short smoke test there (the export code is the same for both).
    samples = SAMPLES[:3] if "1.3B" in name else SAMPLES
    ref = translator.TextTranslator(name, "cpu", print)
    want = [ref.translate(text, src, dst) for src, dst, text in samples]
    del ref
    import gc
    gc.collect()
    onnx_tr = nllb_onnx.OnnxTranslator(name, "cpu", print, providers=["CPUExecutionProvider"])
    same = 0
    for (src, dst, text), a in zip(samples, want):
        b = onnx_tr.translate(text, src, dst)
        same += a == b
        print(f"[{src}->{dst}] {text}\n   ct2 : {a}\n   onnx: {b}", flush=True)
        if not b.strip():
            print("EMPTY translation")
            return 1
    print(f"{same}/{len(samples)} identical")
    # int8 (CTranslate2) and fp16 (ONNX) differ a little; most must still match exactly
    return 0 if same >= max(1, len(samples) * 5 // 8) else 1


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "export":
        export(sys.argv[2], Path(sys.argv[3]))
    elif cmd == "check":
        sys.exit(check(sys.argv[2], Path(sys.argv[3])))
