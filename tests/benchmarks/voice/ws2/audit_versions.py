#!/usr/bin/env python3
"""Audit package versions across venvs."""
from pathlib import Path

venvs = {
    'prod (.venv)': Path('tests/benchmarks/voice/ws2/results/pip_freeze_prod.txt'),
    'ws2 (.venv-ws2-bench)': Path('tests/benchmarks/voice/ws2/results/pip_freeze_ws2.txt'),
    'ws3 (.venv-ws3-bench)': Path('tests/benchmarks/voice/ws2/results/pip_freeze_ws3.txt'),
}

pkgs = ['sherpa-onnx', 'onnxruntime', 'faster-whisper', 'ctranslate2', 'numpy']

data = {}
for venv_name, file_path in venvs.items():
    data[venv_name] = {}
    if file_path.exists():
        content = None
        for enc in ['utf-16', 'utf-8', 'cp1252']:
            try:
                content = file_path.read_text(encoding=enc)
                break
            except Exception:
                pass
        if content:
            for line in content.splitlines():
                line = line.strip()
                for p in pkgs:
                    p_prefix = p.lower() + '=='
                    if line.lower().startswith(p_prefix) or line.lower().startswith(p.replace('-', '_') + '=='):
                        data[venv_name][p] = line.split('==')[1]

print(f"{'Package':<16} | {'prod (.venv)':<18} | {'ws2 (.venv-ws2-bench)':<22} | {'ws3 (.venv-ws3-bench)':<22}")
print("-" * 85)
for p in pkgs:
    v_prod = data['prod (.venv)'].get(p, 'NOT INSTALLED')
    v_ws2 = data['ws2 (.venv-ws2-bench)'].get(p, 'NOT INSTALLED')
    v_ws3 = data['ws3 (.venv-ws3-bench)'].get(p, 'NOT INSTALLED')
    print(f"{p:<16} | {v_prod:<18} | {v_ws2:<22} | {v_ws3:<22}")
