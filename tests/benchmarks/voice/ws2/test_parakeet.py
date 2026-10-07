import onnxruntime as ort
import sherpa_onnx
import os
import traceback

model_dir = "tests/benchmarks/voice/ws2/models_offline/parakeet-tdt-0.6b-v2-int8"
encoder = os.path.join(model_dir, "encoder.int8.onnx")
decoder = os.path.join(model_dir, "decoder.int8.onnx")
joiner = os.path.join(model_dir, "joiner.int8.onnx")
tokens = os.path.join(model_dir, "tokens.txt")

print("Checking ONNX model metadata via onnxruntime...")
for path in [encoder, decoder, joiner]:
    try:
        sess = ort.InferenceSession(path, providers=['CPUExecutionProvider'])
        meta = sess.get_modelmeta().custom_metadata_map
        print(f"{os.path.basename(path)} metadata keys:", list(meta.keys()))
        for k, v in meta.items():
            print(f"  {k} = {v[:80] if len(v) > 80 else v}")
    except Exception as e:
        print(f"Error inspecting {path}: {e}")

print("\n--- ATTEMPT 1: from_transducer with default params ---")
try:
    r = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        tokens=tokens,
        num_threads=1,
    )
    print("SUCCESS with default!")
except Exception as e:
    print("FAILED Attempt 1:")
    traceback.print_exc()

print("\n--- ATTEMPT 2: testing model_type options ---")
for mt in ["nemo_transducer", "tdt", "zipformer", "conformer"]:
    try:
        print(f"Trying model_type='{mt}'...")
        r = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=encoder,
            decoder=decoder,
            joiner=joiner,
            tokens=tokens,
            num_threads=1,
            model_type=mt,
        )
        print(f"SUCCESS with model_type='{mt}'!")
        break
    except Exception as e:
        print(f"FAILED with model_type='{mt}': {e}")
