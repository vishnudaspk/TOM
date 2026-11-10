<script lang="ts">
  import { invoke } from '@tauri-apps/api/core';
  import { listen } from '@tauri-apps/api/event';
  import { onMount } from 'svelte';
  import { Activity, Bot, Box, ChevronRight, CircleHelp, Cpu, Database, Eye, Gauge, HardDrive, Headphones, LayoutDashboard, MonitorCog, Play, RefreshCw, Settings, ShieldCheck, Square, Terminal, Wrench, X } from '@lucide/svelte';
  type Component={id:string;name:string;kind:string;state:string;pid?:number;health:string;started_at?:string;last_error?:string;owner_mode?:string;session_id?:string;lifecycle_state?:string;api_url?:string};
  let active='Overview'; let components:Component[]=[]; let logs:string[]=[]; let telemetry:any={}; let config:any={}; let memory:any={}; let management:any={}; let engineInfo:any={}; let voiceResult:any={}; let busy=''; let notice=''; let noticeKey=''; let noticeLastShownAt=0; let filter=''; let cliLine=''; let interactionMode='python'; let closePrompt=false; let pollError=''; let diagnosticsAt=0;
  const nav=[['Overview',LayoutDashboard],['AI Models',Box],['Rust Engine',Cpu],['Python Runtime',Terminal],['CLI',Terminal],['Voice',Headphones],['Vision Model',Eye],['Desktop Assistant',MonitorCog],['Tools',Wrench],['Memory',Database],['Diagnostics',Activity],['Settings',Settings]] as const;
  const pythonIds=['python','cli'];
  const state=(id:string)=>components.find(x=>x.id===id)?.state??'unknown';
  const componentFor=(id:string)=>components.find(x=>x.id===id)??null;
  const pythonOwner=()=>components.find(c=>c.kind==='process'&&pythonIds.includes(c.id)&&!!c.owner_mode)??null;
  const previousPythonExit=()=>components.find(c=>pythonIds.includes(c.id)&&c.state==='failed')??null;
  const canStart=(id:string)=>['python','cli','engine','model','wheel_script'].includes(id)&&!pythonOwner();
  const modeLabel=(id?:string)=>id==='python'?'Python Service':id==='cli'?'TOM CLI':id==='voice'?'Voice Pipeline':(id??'Python');
  const detailId=()=>active==='AI Models'?'model':active==='Rust Engine'?'engine':active==='Python Runtime'?'python':active==='CLI'?'cli':active==='Voice'?'voice':active==='Vision Model'?'vision':'wheel_script';
  const ownedState=(value:string)=>['starting','ready','running','degraded','stopping'].includes(value);
  const pipelineActive=()=>!!management.status?.voice?.pipeline_active;
  const pipelineStartDisabled=()=>!!busy||!management.available||!management.status||pipelineActive();
  const pipelineStopDisabled=()=>!!busy||!management.available||!pipelineActive();
  const voiceOpsDisabled=()=>!!busy||!management.available||!management.status||pipelineActive();
  function voiceLifecycle(){
    const voice=componentFor('voice');
    if (voice?.lifecycle_state) return String(voice.lifecycle_state).toUpperCase();
    if (!voice||voice.state==='stopped') return 'STOPPED';
    if (voice.state==='starting') return 'STARTING';
    if (voice.state==='degraded') return 'DEGRADED';
    if (voice.state==='failed') return 'FAILED';
    if (voice.state==='ready'||voice.state==='running') return 'READY';
    return 'UNKNOWN';
  }
  function voiceDescription(){
    const lifecycle=voiceLifecycle();
    if (lifecycle==='READY') return 'Voice pipeline is running and the microphone is capturing.';
    if (lifecycle==='STARTING') return 'Voice pipeline is starting; the microphone is not capturing yet.';
    if (lifecycle==='DEGRADED') return 'Voice pipeline is owned by the Python Runtime but its status is degraded.';
    if (lifecycle==='FAILED') return management?.status?.voice?.last_error||componentFor('voice')?.last_error||'Voice pipeline failed.';
    if (lifecycle==='STOPPED') return 'Voice pipeline is stopped inside the Python Runtime. STT and Kokoro can still be loaded and tested independently.';
    return 'Voice status is unavailable.';
  }
  const providerLabel=(provider:any)=>{ if(!provider) return 'not configured'; if(provider.state==='loaded') return `loaded${provider.device?` · ${provider.device}`:''}${provider.model?` · ${provider.model}`:''}`; if(provider.state==='unloaded') return 'unloaded'; if(provider.state==='not_initialized') return 'not initialized'; return provider.state||'configured'; };
  function openComponent(component:Component){ if(['voice','stt','tts'].includes(component.id)) active='Voice'; else if(component.id==='vision') active='Vision Model'; }
  const processComponents=()=>components.filter(c=>c.kind==='process');
  const voiceComponents=()=>components.filter(c=>c.kind==='capability');
  const futureComponents=()=>components.filter(c=>c.kind==='placeholder');
  function setNotice(message:string, key?:string, cooldownMs=20000){
    const normalized = message.trim();
    if (!normalized) {
      notice='';
      noticeKey='';
      noticeLastShownAt=0;
      return;
    }
    const fingerprint = (key ?? normalized).trim();
    const now = Date.now();
    if (noticeKey === fingerprint && now - noticeLastShownAt < cooldownMs) {
      return;
    }
    notice = normalized;
    noticeKey = fingerprint;
    noticeLastShownAt = now;
  }
  async function refresh(){
    const [status, hardware, supervisorLogs, memoryData, diagnostics] = await Promise.allSettled([
      invoke<Component[]>('component_status'),
      invoke<any>('telemetry'),
      invoke<string[]>('get_logs'),
      invoke<any>('memory_status'),
      invoke<any>('python_diagnostics')
    ]);
    if (status.status==='fulfilled') components=status.value;
    if (hardware.status==='fulfilled') telemetry=hardware.value;
    if (supervisorLogs.status==='fulfilled') logs=supervisorLogs.value;
    if (memoryData.status==='fulfilled') memory=memoryData.value;
    if (diagnostics.status==='fulfilled') {
      management=diagnostics.value;
      diagnosticsAt=Date.now();
    } else {
      management={available:false,reason:String(diagnostics.reason)};
      diagnosticsAt=0;
    }
    const failures=[status,diagnostics].filter(result=>result.status==='rejected').map(result=>String((result as PromiseRejectedResult).reason));
    pollError=failures.length?failures.join(' | '):'';
  }
  async function act(id:string,verb:'start_component'|'stop_component'|'restart_component'){busy=id;setNotice('', '');try{await invoke(verb,{id});setNotice(`${modeLabel(id)} ${verb.replace('_component','')} requested`, `action:${id}:${verb}`);await refresh()}catch(e){setNotice(String(e),`action:${id}:${verb}:error`)}finally{busy=''}}
  onMount(()=>{refresh();invoke('get_config').then(v=>config=v).catch(e=>setNotice(String(e),`config:load:error`));const timer=setInterval(refresh,5000);let unlisten:(()=>void)|undefined;let closeUnlisten:(()=>void)|undefined;listen<string>('tom-log',e=>{logs=[...logs,e.payload].slice(-1500)}).then(u=>unlisten=u);listen('close-policy-required',()=>closePrompt=true).then(u=>closeUnlisten=u);return()=>{clearInterval(timer);unlisten?.();closeUnlisten?.()}});
  async function save(){try{await invoke('save_config',{config});setNotice('Configuration saved','config:saved')}catch(e){setNotice(String(e),'config:saved:error')}}
  async function probe(){try{const v=await invoke('probe_model');setNotice(`Model endpoint responds: ${JSON.stringify(v).slice(0,160)}`,'probe:model:ok')}catch(e){setNotice(`Model health check failed: ${e}`,'probe:model:error')}}
  async function startTom(){busy='all';try{const ids=await invoke<string[]>('start_tom',{mode:interactionMode});setNotice(`TOM startup ready: ${ids.join(', ')}`,'start:tom:ok')}catch(e){setNotice(String(e),'start:tom:error')}finally{busy='';await refresh()}}
  async function probeEngine(){try{engineInfo=await invoke('engine_diagnostics');setNotice('Engine diagnostics refreshed','engine:diag:ok')}catch(e){setNotice(String(e),'engine:diag:error')}}
  async function voiceOp(operation:string,payload:any={}){if(!management.available){setNotice('Python diagnostics are unavailable because no owned Python session has a reachable management API.',`voice:${operation}:unavailable`);return}busy=operation;setNotice('', '');try{voiceResult=await invoke('python_voice_operation',{operation,payload});await refresh()}catch(e){setNotice(String(e),`voice:${operation}:error`)}finally{busy=''}}
  async function closeWith(policy:'keep'|'stop'|'cancel'){try{await invoke('resolve_close',{policy});closePrompt=false}catch(e){setNotice(String(e),'close:policy:error')}}
  async function stopTom(){busy='all';try{const ids=await invoke<string[]>('stop_tom');setNotice(`Stopped owned components: ${ids.join(', ')||'none'}`,'stop:tom:ok')}catch(e){setNotice(String(e),'stop:tom:error')}finally{busy='';await refresh()}}
  async function sendLine(){try{await invoke('send_cli_input',{line:cliLine});logs=[...logs,`[CLI INPUT] ${cliLine}`].slice(-1500);cliLine=''}catch(e){setNotice(String(e),'cli:send:error')}}
  $: owner=pythonOwner();
  $: previousExit=previousPythonExit();
  $: shown=components.filter(c=>!filter||c.name.toLowerCase().includes(filter.toLowerCase())||c.id.includes(filter.toLowerCase()));
  $: voiceState=voiceLifecycle();
  $: voiceText=voiceDescription();
</script>

<svelte:head><title>TOM Control Center</title><meta name="description" content="Local TOM runtime supervisor"/></svelte:head>
<div class="shell">
  <aside class="sidebar"><div class="brand"><div class="brand-mark"><Bot size={22}/></div><div><b>TOM</b><small>CONTROL CENTER</small></div><span class="local"><i></i>LOCAL</span></div><div class="nav-label">WORKSPACE</div><nav>{#each nav as [label,Icon]}<button class:active={active===label} onclick={()=>active=label}><Icon size={17}/><span>{label}</span>{#if active===label}<ChevronRight size={14}/>{/if}</button>{/each}</nav><div class="sidebar-foot"><div class="secure"><ShieldCheck size={16}/><span>Local runtime<br/><small>No cloud connection</small></span></div><div class="version">TOM Control Center <span>v0.1</span></div></div></aside>
  <main><header><div><div class="eyebrow">TOM / {active.toUpperCase()}</div><h1>{active}</h1></div><div class="top-actions"><span class="sync"><i></i>LIVE STATUS</span><button class="icon-btn" title="Refresh" onclick={refresh}><RefreshCw size={16}/></button><select bind:value={interactionMode} aria-label="TOM interaction mode"><option value="python">Python Service</option><option value="cli">CLI</option></select><button class="primary" onclick={startTom} disabled={!!busy}><Play size={15} fill="currentColor"/> Start TOM</button><button class="danger" onclick={stopTom} disabled={!!busy}><Square size={14} fill="currentColor"/> Stop TOM</button></div></header>
  {#if pollError}<div class="notice" style="background:#33272a;border-color:#6d4a50;color:#f0c5cc">Supervisor polling failed: {pollError}<button onclick={()=>pollError=''} aria-label="Dismiss polling error"><X size={14}/></button></div>{/if}
  {#if notice}<div class="notice">{notice}<button onclick={()=>notice=''} aria-label="Dismiss notice"><X size={14}/></button></div>{/if}
  <section class="panel" style="display:flex;gap:16px;align-items:center;justify-content:space-between;margin-bottom:14px">
    {#if owner}
      <div><div class="eyebrow">PYTHON SESSION OWNER</div><b>{modeLabel(owner.owner_mode)} · PID {owner.pid}</b> <span class="pill" class:good={owner.state==='ready'||owner.state==='running'}><i></i>{(owner.lifecycle_state||owner.state).toUpperCase()}</span></div>
      <div style="text-align:right"><p class="muted">Session {owner.session_id?.slice(0,12)||'unknown'} · another Python Service or CLI mode cannot start until this session stops. Voice is managed inside this runtime.</p>{#if owner.api_url}<p class="limited">{owner.api_url}</p>{/if}</div>
    {:else if previousExit}
      <div><div class="eyebrow">PREVIOUS PYTHON SESSION EXITED</div><b>{modeLabel(previousExit.id)} ownership was released</b></div>
      <div style="text-align:right"><p class="muted">{previousExit.last_error||'No exit detail was captured.'}</p><p class="limited">Diagnostics remain unavailable until a new Python session starts.</p></div>
    {:else}
      <div><div class="eyebrow">PYTHON SESSION OWNER</div><b>No Python session is owned</b></div>
      <div style="text-align:right"><p class="muted">Start the Python Service or CLI to own the authoritative Python runtime and expose authenticated diagnostics.</p>{#if diagnosticsAt===0}<p class="limited">Python management API is not currently reachable.</p>{/if}</div>
    {/if}
  </section>
  {#if active==='Overview'}
    <div class="welcome"><div><div class="eyebrow">SYSTEM CONTROL / LOCAL AI</div><h2>Your TOM runtime,<br/><em>at a glance.</em></h2><p>Monitor and manage your local assistant components from one place.</p></div><div class="orb"><div class="orb-core"><Bot size={38}/></div><div class="orbit orbit1"></div><div class="orbit orbit2"></div><span class="orbit-dot"></span></div><div class="overall"><span class="dot" class:online={components.some(c=>c.state==='running'||c.state==='ready')}></span><div><strong>{components.some(c=>c.state==='running'||c.state==='ready')?'RUNTIME ACTIVE':'RUNTIME IDLE'}</strong><small>Components launch only when requested</small></div></div></div>
    <div class="metric-row"><div class="metric"><div class="metric-head"><span>CPU USAGE</span><Cpu size={16}/></div><b>{Number(telemetry.cpu_percent||0).toFixed(1)}<small>%</small></b><div class="meter"><i style={`width:${Math.min(100,telemetry.cpu_percent||0)}%`}></i></div></div><div class="metric"><div class="metric-head"><span>SYSTEM MEMORY</span><HardDrive size={16}/></div><b>{Number(telemetry.ram_percent||0).toFixed(1)}<small>%</small></b><div class="meter"><i style={`width:${Math.min(100,telemetry.ram_percent||0)}%`}></i></div><small class="muted">{telemetry.ram_used_mb||0} / {telemetry.ram_total_mb||0} MB</small></div><div class="metric"><div class="metric-head"><span>GPU / VRAM</span><Gauge size={16}/></div><b>{telemetry.gpu?.available?`${telemetry.gpu.vram_free_mb} MB free`:`Unavailable`}</b><div class="metric-note">{telemetry.gpu?.available?`${telemetry.gpu.name} · ${telemetry.gpu.utilization_percent}% · ${telemetry.gpu.temperature_c}C`:telemetry.gpu?.reason||'nvidia-smi unavailable'}</div></div><div class="metric"><div class="metric-head"><span>ACTIVE TOM PROCESSES</span><Activity size={16}/></div><b>{telemetry.processes?.length||0}<small> tracked</small></b><div class="metric-note">System process list · live</div></div></div>
    <div class="section-head"><div><div class="eyebrow">CORE RUNTIME</div><h3>Supervised processes</h3></div><span class="hint">Only this supervisor launches and owns these processes</span></div>
    <div class="component-grid">{#each processComponents() as c}<article class="component-card"><div class="card-top"><span class="service-icon"><Cpu size={18}/></span><span class="pill" class:good={c.state==='ready'||c.state==='running'}><i></i>{c.state.toUpperCase()}</span></div><h4>{c.name}</h4><p>{c.health}</p><div class="card-bottom"><span>{c.pid?`PID ${c.pid}`:'No owned process'}</span><div>{#if c.pid && ownedState(c.state)}<button class="icon-btn" onclick={()=>act(c.id,'stop_component')} disabled={!!busy}><Square size={14}/></button>{:else}<button class="mini-start" onclick={()=>act(c.id,'start_component')} disabled={!!busy||!canStart(c.id)} title={!canStart(c.id)&&owner?`Owned by ${modeLabel(owner.owner_mode)} PID ${owner.pid}`:''}><Play size={12}/> Start</button>{/if}</div></div></article>{/each}</div>
    <div class="section-head"><div><div class="eyebrow">VOICE CAPABILITIES</div><h3>Managed inside the Python Runtime</h3></div><span class="hint">No separate voice process or duplicate model instance</span></div>
    <div class="component-grid">{#each voiceComponents() as c}<article class="component-card"><div class="card-top"><span class="service-icon"><Headphones size={18}/></span><span class="pill" class:good={c.state==='ready'||c.state==='running'}><i></i>{c.state.toUpperCase()}</span></div><h4>{c.name}</h4><p>{c.health}</p><div class="card-bottom"><span>{c.owner_mode?`Managed by ${modeLabel(c.owner_mode)}`:'Python Runtime unavailable'}</span><button class="mini-start" onclick={()=>openComponent(c)}>Open Voice</button></div></article>{/each}</div>
    <div class="section-head"><div><div class="eyebrow">FUTURE</div><h3>Planned capabilities</h3></div><span class="hint">Clearly non-functional until implemented</span></div>
    <div class="component-grid">{#each futureComponents() as c}<article class="component-card"><div class="card-top"><span class="service-icon"><Eye size={18}/></span><span class="pill"><i></i>{c.state.toUpperCase()}</span></div><h4>{c.name}</h4><p>{c.health}</p><div class="card-bottom"><span>No runtime or management route</span><button class="mini-start" onclick={()=>openComponent(c)}>Details</button></div></article>{/each}</div>    <div class="lower-grid"><section class="panel"><div class="panel-head"><div><div class="eyebrow">OBSERVABILITY</div><h3>Recent logs</h3></div><button class="text-btn" onclick={()=>active='Diagnostics'}>View all <ChevronRight size={13}/></button></div><div class="logs">{#each logs.slice(-5).reverse() as line}<div class="logline"><span>{line.slice(11,19)}</span><code>{line.slice(line.indexOf(']')+2)}</code></div>{:else}<div class="empty">No supervisor logs yet.</div>{/each}</div></section><section class="panel memory-panel"><div class="panel-head"><div><div class="eyebrow">PERSISTENCE</div><h3>Memory service</h3></div><Database size={17}/></div><div class="mem-state"><span class="dot" class:online={memory.available}></span>{memory.available?'Database file found':'Database not initialized'}</div><div class="mem-path">{memory.path}</div><div class="mem-size">{(memory.size_bytes/1024/1024||0).toFixed(2)} MB <span>SQLite · private content hidden</span></div></section></div>
  {:else if active==='Diagnostics'}
    <div class="section-head"><div><div class="eyebrow">SUPERVISOR EVENT STREAM</div><h3>Diagnostics & logs</h3></div><button class="secondary" onclick={()=>navigator.clipboard.writeText(logs.join('\n'))}>Copy logs</button></div><section class="panel diagnostic"><div class="log-toolbar"><span>{logs.length} buffered events</span><input bind:value={filter} placeholder="Filter components"/></div>{#each logs.slice().reverse().filter(l=>!filter||l.toLowerCase().includes(filter.toLowerCase())) as line}<div class="logline"><span>{line.slice(0,19)}</span><code>{line.slice(20)}</code></div>{:else}<div class="empty">No events recorded yet.</div>{/each}</section>
  {:else if active==='Settings'}
    <div class="section-head"><div><div class="eyebrow">LOCAL CONFIGURATION</div><h3>Runtime paths & model settings</h3></div><button class="primary" onclick={save}>Save configuration</button></div><section class="panel settings">{#each [['repo','TOM repository'],['python','Python executable'],['engine','Rust Engine executable'],['llama_server','llama-server executable'],['model','Model file'],['ahk_executable','AutoHotkey v2 executable'],['model_endpoint','Model endpoint'],['wheel_script','Command Wheel script'],['wheel_endpoint','Wheel backend health URL']] as [key,label]}<label>{label}<input bind:value={config[key]} placeholder={label}/></label>{/each}<label>Context size<input type="number" bind:value={config.context_size}/></label><label>GPU layers (-ngl)<input type="number" bind:value={config.gpu_layers}/></label><div class="settings-note">Settings persist in the TOM application data directory. Endpoints must remain loopback-only.</div></section>
  {:else if active==='CLI'}
    <div class="section-head"><div><div class="eyebrow">INTERACTIVE LOCAL PROCESS</div><h3>TOM CLI terminal</h3></div><div class="action-row"><button class="secondary" onclick={()=>act('cli','start_component')} disabled={!!busy||!canStart('cli')} title={!canStart('cli')&&owner?`Owned by ${modeLabel(owner.owner_mode)} PID ${owner.pid}`:''}><Play size={14}/> Launch CLI</button><button class="secondary" onclick={()=>act('cli','stop_component')}><Square size={13}/> Stop session</button><button class="secondary" onclick={()=>logs=[]}>Clear view</button></div></div><section class="panel terminal"><div class="term-label"><span><i></i> STDOUT / STDERR · TOM CLI</span><span>{state('cli').toUpperCase()}</span></div><div class="term-output">{#each logs.filter(l=>l.includes('[cli:')||l.includes('[cli]')||l.includes('[CLI INPUT]')).slice(-160) as line}<div>{line}</div>{:else}<div class="empty">Start TOM CLI to stream its output here.</div>{/each}</div><form class="term-input" onsubmit={(e)=>{e.preventDefault();sendLine()}}><span>›</span><input bind:value={cliLine} placeholder="Send input to TOM CLI…"/><button class="primary" type="submit">Send</button></form></section>
  {:else if active==='Voice'}
    {#if !management.available}
      <section class="panel info-panel" style="display:block"><b>Voice diagnostics unavailable</b><p>{management.reason||'Python management API is not reachable'}. No previous snapshot is presented as live data.</p><button class="secondary" onclick={refresh}>Retry diagnostics</button></section>
    {:else}
      <div class="section-head"><div><div class="eyebrow">AUTHORITATIVE PYTHON RUNTIME</div><h3>Voice pipeline</h3></div><div class="action-row"><button class="secondary" onclick={()=>voiceOp('status')} disabled={!!busy}>Refresh status</button><button class="primary" onclick={()=>voiceOp('start_pipeline')} disabled={pipelineStartDisabled()}><Play size={14}/> Start pipeline</button><button class="danger" onclick={()=>voiceOp('stop_pipeline')} disabled={pipelineStopDisabled()}><Square size={13}/> Stop pipeline</button></div></div>
      <section class="panel info-panel" style="display:block"><div class="card-top"><div><div class="eyebrow">VOICE LIFECYCLE</div><b>{voiceState}</b></div><span class="pill" class:good={voiceState==='READY'}><i></i>{voiceState}</span></div><p>{voiceText}</p><dl class="detail-grid" style="display:grid;grid-template-columns:150px 1fr;gap:8px;margin:14px 0"><dt>Pipeline</dt><dd>{pipelineActive()?'active':'inactive'}{management.status?.voice?.pipeline_state?` · ${management.status.voice.pipeline_state}`:''}</dd><dt>Microphone</dt><dd>{management.status?.voice?.microphone?.state||'unknown'}{management.status?.voice?.microphone?.device?` · ${management.status.voice.microphone.device}`:''}</dd><dt>Captured samples</dt><dd>{management.status?.voice?.audio?.captured_samples??'n/a'} (current capture session)</dd>{#if management.status?.voice?.last_error}<dt>Last error</dt><dd>{management.status.voice.last_error}</dd>{/if}</dl><p class="limited">The microphone starts only when you request Start pipeline. STT and Kokoro are not loaded automatically. Stop the pipeline before independent provider tests or unload operations.</p></section>
      <div class="detail-grid">
        <section class="panel"><div class="card-top"><div><div class="eyebrow">SPEECH-TO-TEXT</div><h3>Faster-Whisper</h3></div><span class="pill" class:good={management.status?.voice?.stt?.state==='loaded'}><i></i>{providerLabel(management.status?.voice?.stt)}</span></div><p>Reuses the single configured STT provider. CUDA loads remain subject to the existing VRAM safety budget.</p><div class="action-row"><button class="secondary" onclick={()=>voiceOp('load_stt')} disabled={voiceOpsDisabled()||management.status?.voice?.stt?.state==='loaded'}>Load STT</button><button class="secondary" onclick={()=>voiceOp('unload_stt')} disabled={voiceOpsDisabled()||management.status?.voice?.stt?.state!=='loaded'}>Unload STT</button><button class="secondary" onclick={()=>voiceOp('test_stt',{seconds:3})} disabled={voiceOpsDisabled()}>Record and transcribe (3s)</button></div></section>
        <section class="panel"><div class="card-top"><div><div class="eyebrow">TEXT-TO-SPEECH</div><h3>Kokoro CPU</h3></div><span class="pill" class:good={management.status?.voice?.tts?.state==='loaded'}><i></i>{providerLabel(management.status?.voice?.tts)}</span></div><p>Kokoro remains CPU-only, so it does not compete with the LLM or STT for VRAM.</p><div class="action-row"><button class="secondary" onclick={()=>voiceOp('load_tts')} disabled={voiceOpsDisabled()||management.status?.voice?.tts?.state==='loaded'}>Load Kokoro</button><button class="secondary" onclick={()=>voiceOp('unload_tts')} disabled={voiceOpsDisabled()||management.status?.voice?.tts?.state!=='loaded'}>Unload Kokoro</button><button class="secondary" onclick={()=>voiceOp('test_tts',{text:'TOM voice synthesis test.'})} disabled={voiceOpsDisabled()}>Synthesize and play test</button></div></section>
      </div>
      {#if Object.keys(voiceResult).length}<details class="panel" style="margin-top:14px"><summary>Last voice operation result</summary><pre>{JSON.stringify(voiceResult,null,2)}</pre></details>{/if}
    {/if}
  {:else if active==='Vision Model'}
    <div class="section-head"><div><div class="eyebrow">FUTURE CAPABILITY</div><h3>Vision Model</h3></div><span class="pill"><i></i>UNSUPPORTED</span></div>
    <section class="panel info-panel"><Eye size={20}/><div><b>Not implemented</b><p>No vision runtime, model file, GPU admission path, or management API exists yet. This placeholder does not start a process, download a model, or reserve VRAM.</p><p class="limited">When implemented, it must follow the same Control Center → Rust Supervisor → Authenticated Python Management API → existing TOM components path.</p></div></section>
  {:else if !['Tools','Memory'].includes(active)}
    <div class="section-head"><div><div class="eyebrow">COMPONENT CONTROL</div><h3>{active} runtime</h3></div><div class="action-row"><button class="secondary" onclick={refresh}><RefreshCw size={14}/> Health check</button></div></div><div class="detail-grid"><section class="panel detail-main"><div class="detail-title"><span class="service-icon large"><Cpu size={22}/></span><div><h2>{active}</h2><span class="pill" class:good={components.some(c=>c.state==='ready'||c.state==='running')}><i></i>{state(detailId())}</span></div></div><p class="detail-desc">Supervisor-managed local component. Process ownership is recorded only when launched by this application.</p><div class="detail-actions"><button class="primary" onclick={()=>act(detailId(),'start_component')} disabled={!!busy||!canStart(detailId())} title={!canStart(detailId())&&owner?`Owned by ${modeLabel(owner.owner_mode)} PID ${owner.pid}`:''}><Play size={14}/> Start</button><button class="secondary" onclick={()=>act(detailId(),'restart_component')} disabled={!!busy||!canStart(detailId())}><RefreshCw size={14}/> Restart</button><button class="secondary" onclick={()=>act(detailId(),'stop_component')} disabled={!!busy}><Square size={13}/> Stop owned process</button>{#if active==='AI Models'}<button class="secondary" onclick={probe}>Probe model API</button>{/if}</div></section><section class="panel"><div class="eyebrow">STATUS DETAILS</div>{#each components.filter(c=>c.id===detailId()) as c}<dl><dt>State</dt><dd>{c.state}</dd>{#if c.lifecycle_state}<dt>Lifecycle</dt><dd>{c.lifecycle_state}</dd>{/if}<dt>Health</dt><dd>{c.health}</dd><dt>Process ID</dt><dd>{c.pid??'—'}</dd><dt>Started at</dt><dd>{c.started_at??'—'}</dd>{#if c.owner_mode}<dt>Owner mode</dt><dd>{modeLabel(c.owner_mode)}</dd>{/if}{#if c.session_id}<dt>Session</dt><dd>{c.session_id}</dd>{/if}{#if c.api_url}<dt>Control API</dt><dd>{c.api_url}</dd>{/if}{#if c.last_error}<dt>Last error</dt><dd>{c.last_error}</dd>{/if}</dl>{/each}<p class="limited">Read-only Python diagnostics are available while a managed Python Service or CLI session is active.</p></section></div>
    {#if active==='Rust Engine'}<section class="panel info-panel"><div><b>Engine subsystem diagnostics</b><button class="secondary" onclick={probeEngine}>Probe Engine, audio, hotkeys, telemetry</button>{#if Object.keys(engineInfo).length}<pre>{JSON.stringify(engineInfo,null,2)}</pre>{:else}<p>Queries use the existing named-pipe NDJSON v1 methods. Unsupported or failed responses are reported per method.</p>{/if}</div></section>{/if}
    {#if ['Tools','Memory'].includes(active)}<section class="panel info-panel"><CircleHelp size={18}/><div><b>{active==='Tools'?'Permission-safe diagnostics':'Memory status'}</b><p>{active==='Tools'?'Tool execution remains in the Python ToolRegistry → PermissionEngine → ConfirmationHook → ToolExecutor chain. This UI exposes no permission bypass or direct execution.':'SQLite availability and file size are displayed on Overview. Memory contents remain private and are never shown.'}</p></div></section>{/if}
  {/if}
  {#if active==='Tools'}<section class="panel info-panel"><Wrench size={18}/><div><b>Live Python ToolRegistry</b>{#if management.tools?.tools}<p>{management.tools.tools.length} registered tools. Read-only metadata; TOM retains all execution and permission checks.</p>{#each management.tools.tools as tool}<div class="tool-row"><b>{tool.name}</b><span>{tool.permission}</span><small>{tool.description}</small></div>{/each}{:else}<p>{management.reason||'Start a Python session to inspect its live registry.'}</p>{/if}</div></section>{/if}
  {#if active==='Memory'}<section class="panel info-panel"><Database size={18}/><div><b>Read-only TOM memory diagnostics</b>{#if management.memory?.sqlite}<p>Integrity: {management.memory.sqlite.integrity}; present: {management.memory.sqlite.exists}; {management.memory.sqlite.size_bytes} bytes.</p><small>{management.memory.sqlite.error||management.memory.sqlite.path}</small>{:else}<p>{management.reason||'Start a Python session to inspect memory connectivity. Memory contents remain hidden.'}</p>{/if}</div></section>{/if}
  <footer><span><i></i> SUPERVISOR CONNECTED</span><span>LOCAL MACHINE <b>·</b> NO REMOTE TELEMETRY</span></footer></main>
</div>
{#if closePrompt}<div style="position:fixed;inset:0;background:#000b;display:grid;place-items:center;z-index:20"><section class="panel" style="max-width:480px;padding:28px;display:grid;gap:14px"><h2>Close TOM Control Center?</h2><p>Choose what happens to components started by this supervisor.</p><button class="primary" onclick={()=>closeWith('keep')}>Keep TOM running and close</button><button class="danger" onclick={()=>closeWith('stop')}>Stop managed components and close</button><button class="secondary" onclick={()=>closeWith('cancel')}>Cancel</button></section></div>{/if}
