import asyncio
import numpy as np
from tom.voice.stt import FasterWhisperSTTProvider, STTConfig
from tom.schemas.voice import TranscriptionRequest

async def test():
    config = STTConfig(device='cuda', compute_type='int8_float16')
    provider = FasterWhisperSTTProvider(config=config)
    
    # Generate 1 second of test audio (sine wave)
    sample_rate = 16000
    duration = 2.0
    t = np.linspace(0, duration, int(sample_rate * duration))
    audio = np.sin(2 * np.pi * 440 * t) * 0.1 + np.sin(2 * np.pi * 880 * t) * 0.05
    samples = (audio * 32767).astype(np.int16).tobytes()
    
    request = TranscriptionRequest(samples=samples, sample_rate=16000, channels=1)
    result = await provider.transcribe(request)
    print(f'STT Result: "{result.text}"')
    print(f'Latency: {result.latency_ms}ms')
    print(f'Model: {result.model_name}')
    print(f'Provider: {result.provider}')

asyncio.run(test())