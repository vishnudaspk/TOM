"""Bootstrap script for initial TOM directory tree."""

from pathlib import Path

dirs = [
    "python/tom/core",
    "python/tom/agents",
    "python/tom/models",
    "python/tom/resources",
    "python/tom/memory",
    "python/tom/tools",
    "python/tom/security",
    "python/tom/vision",
    "python/tom/voice",
    "python/tom/ipc",
    "python/tom/telemetry",
    "python/tom/schemas",
    "rust/tom-engine",
    "config",
    "tests/unit/core",
    "tests/unit/telemetry",
    "tests/integration",
    "scripts",
    "data/memory",
    "data/logs",
    "data/cache",
    "data/runtime",
    "models",
]

for d in dirs:
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    if d.startswith("python/tom/"):
        init_file = p / "__init__.py"
        if not init_file.exists():
            pkg_name = d.split("/")[-1]
            init_file.write_text(f'"""TOM {pkg_name} subsystem."""\n', encoding="utf-8")
    elif d.startswith("data/") or d == "models" or d == "rust/tom-engine":
        gk = p / ".gitkeep"
        if not gk.exists():
            gk.touch()

print("Directories scaffolded successfully.")
