from tom.main import TomApp

app = TomApp()
print('TomApp created successfully')
print('Testing config loading...')
if app._config:
    print(f'STT device: {app._config.models.stt.device}')
    print(f'STT compute_type: {app._config.models.stt.compute_type}')
    print(f'STT mode: {app._config.models.stt.mode}')
else:
    print('No config loaded')