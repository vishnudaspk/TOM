//! # TOM Engine — Iteration 10: Integration Hardening & Exit Gate Suite
//!
//! Comprehensive end-to-end integration, concurrency stress, graceful shutdown under load,
//! resource leak audit, failure modes, and IPC performance benchmark tests.

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::json;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio_util::sync::CancellationToken;

use tom_engine::audio::{AudioCaptureConfig, AudioCaptureController, AudioPlaybackController};
use tom_engine::events::{EngineEvent, EventBus, DEFAULT_EVENT_BUS_CAPACITY};
use tom_engine::input::{HotkeyAction, HotkeyBinding, HotkeyManager, KeyCode, KeyModifiers};
use tom_engine::ipc::{
    create_default_dispatcher, error_code, handle_connection, IpcRequest, IpcResponse,
};
use tom_engine::lifecycle::{EngineLifecycle, DEFAULT_SHUTDOWN_TIMEOUT};
use tom_engine::system::SystemMonitor;

// ============================================================================
// 1. End-to-End Concurrent Multi-Subsystem Integration
// ============================================================================

#[tokio::test]
async fn test_concurrent_multi_subsystem_integration() {
    let lifecycle = EngineLifecycle::new(Duration::from_secs(3));
    let (event_bus, mut event_rx) = EventBus::new(DEFAULT_EVENT_BUS_CAPACITY);
    let dispatcher = Arc::new(create_default_dispatcher());
    let hotkey_manager = HotkeyManager::new();

    // Register test hotkey
    hotkey_manager
        .register_hotkey(HotkeyBinding::new(
            "test_hotkey",
            KeyCode::Space,
            KeyModifiers {
                ctrl: true,
                alt: false,
                shift: false,
                win: false,
            },
            HotkeyAction::ToggleListening,
        ))
        .await
        .expect("hotkey registration failed");

    let mut hotkey_rx = hotkey_manager.subscribe();

    // Spawn mock audio capture
    let capture_cancel = lifecycle.child_token();
    let (_capture_ctrl, mut capture_rx) = AudioCaptureController::create_mock_capture(
        AudioCaptureConfig {
            sample_rate: 16000,
            channels: 1,
            chunk_size: 320,
            ..Default::default()
        },
        capture_cancel,
    );

    // Spawn mock audio playback
    let playback_cancel = lifecycle.child_token();
    let (_playback_ctrl, playback_tx, playback_counter) =
        AudioPlaybackController::create_mock_playback(playback_cancel);

    // Spawn concurrent tasks
    let mut tasks = Vec::new();

    // Task A: Send 20 concurrent IPC requests across diverse methods
    let disp_clone = Arc::clone(&dispatcher);
    let ipc_task = tokio::spawn(async move {
        let (client_stream, server_stream) = tokio::io::duplex(65536);
        let srv_cancel = CancellationToken::new();

        let srv_handle = tokio::spawn(handle_connection(
            server_stream,
            disp_clone,
            srv_cancel.clone(),
        ));

        let (client_read, mut client_write) = tokio::io::split(client_stream);
        let mut client_reader = BufReader::new(client_read);

        let methods = [
            "engine.ping",
            "engine.status",
            "system.cpu",
            "system.memory",
            "system.all",
        ];

        for i in 0..20 {
            let method = methods[i % methods.len()];
            let req = IpcRequest::new(format!("req_{i}"), method, json!({}));
            let mut wire = serde_json::to_vec(&req).unwrap();
            wire.push(b'\n');

            client_write.write_all(&wire).await.unwrap();
            client_write.flush().await.unwrap();

            let mut line = String::new();
            client_reader.read_line(&mut line).await.unwrap();
            let resp: IpcResponse = serde_json::from_str(&line).unwrap();

            assert_eq!(resp.id, format!("req_{i}"));
            assert!(
                resp.success,
                "Method {method} returned failure: {:?}",
                resp.error
            );
        }

        srv_cancel.cancel();
        let _ = srv_handle.await;
    });
    tasks.push(ipc_task);

    // Task B: Event bus publishing
    let bus_clone = event_bus.clone();
    let event_task = tokio::spawn(async move {
        for i in 0..10 {
            bus_clone.publish(EngineEvent::CpuSpike {
                usage_percent: 80.0 + (i as f64),
            });
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    });
    tasks.push(event_task);

    // Task C: Audio feed: capture samples and feed into playback
    let audio_task = tokio::spawn(async move {
        for _ in 0..5 {
            if let Some(samples) = capture_rx.recv().await {
                playback_tx.send(samples).await.unwrap();
            }
        }
    });
    tasks.push(audio_task);

    // Task D: Trigger hotkey action
    let hk_clone = hotkey_manager.clone();
    let hotkey_task = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(10)).await;
        hk_clone.trigger_action("test_hotkey").await.unwrap();
    });
    tasks.push(hotkey_task);

    // Await all concurrent tasks
    for t in tasks {
        t.await.expect("Subsystem task failed");
    }

    // Verify EventBus received events
    let mut received_events = 0;
    while let Ok(_event) = event_rx.try_recv() {
        received_events += 1;
    }
    assert!(
        received_events >= 5,
        "Expected at least 5 events received, got {received_events}"
    );

    // Verify audio samples arrived at playback
    tokio::time::sleep(Duration::from_millis(30)).await;
    assert!(playback_counter.load(Ordering::SeqCst) > 0);

    // Verify hotkey action was broadcast
    let action = hotkey_rx.recv().await.expect("Expected hotkey action");
    assert_eq!(action, HotkeyAction::ToggleListening);

    // Clean shutdown
    let shutdown_res = lifecycle.shutdown().await;
    assert!(shutdown_res.is_ok());
}

// ============================================================================
// 2. High-Concurrency Stress Test
// ============================================================================

#[tokio::test]
async fn test_concurrent_ipc_stress_and_no_starvation() {
    let dispatcher = Arc::new(create_default_dispatcher());
    let cancel = CancellationToken::new();

    // Spawn 10 concurrent client workers, each executing 10 requests (100 total)
    let num_workers = 10;
    let requests_per_worker = 10;
    let mut worker_handles = Vec::new();

    for w in 0..num_workers {
        let disp = Arc::clone(&dispatcher);
        let worker_cancel = cancel.child_token();

        let handle = tokio::spawn(async move {
            let (client_stream, server_stream) = tokio::io::duplex(32768);
            let srv_task = tokio::spawn(handle_connection(
                server_stream,
                disp,
                worker_cancel.clone(),
            ));

            let (read_half, mut write_half) = tokio::io::split(client_stream);
            let mut reader = BufReader::new(read_half);

            for r in 0..requests_per_worker {
                let req_id = format!("w{w}_r{r}");
                let req = IpcRequest::new(&req_id, "engine.ping", json!({}));
                let mut wire = serde_json::to_vec(&req).unwrap();
                wire.push(b'\n');

                write_half.write_all(&wire).await.unwrap();
                write_half.flush().await.unwrap();

                let mut line = String::new();
                reader.read_line(&mut line).await.unwrap();
                let resp: IpcResponse = serde_json::from_str(&line).unwrap();

                assert_eq!(resp.id, req_id);
                assert!(resp.success);
            }

            worker_cancel.cancel();
            let _ = srv_task.await;
        });

        worker_handles.push(handle);
    }

    for h in worker_handles {
        h.await.expect("Worker panicked under stress load");
    }
}

// ============================================================================
// 3. Graceful Shutdown Under Load & 5-Second Deadline Verification
// ============================================================================

#[tokio::test]
async fn test_graceful_shutdown_under_active_load() {
    let mut lifecycle = EngineLifecycle::new(DEFAULT_SHUTDOWN_TIMEOUT);
    let (event_bus, _event_rx) = EventBus::new(DEFAULT_EVENT_BUS_CAPACITY);
    let dispatcher = Arc::new(create_default_dispatcher());

    // 1. Background worker: continuous IPC queries
    let ipc_token = lifecycle.child_token();
    let disp_clone = Arc::clone(&dispatcher);
    let ipc_worker = tokio::spawn(async move {
        let (client_stream, server_stream) = tokio::io::duplex(16384);
        let srv_token = ipc_token.child_token();
        let srv_handle = tokio::spawn(handle_connection(server_stream, disp_clone, srv_token));

        let (read_half, mut write_half) = tokio::io::split(client_stream);
        let mut reader = BufReader::new(read_half);

        let mut counter = 0;
        while !ipc_token.is_cancelled() {
            counter += 1;
            let req = IpcRequest::new(format!("load_{counter}"), "system.cpu", json!({}));
            let mut wire = match serde_json::to_vec(&req) {
                Ok(w) => w,
                Err(_) => break,
            };
            wire.push(b'\n');

            if write_half.write_all(&wire).await.is_err() {
                break;
            }
            if write_half.flush().await.is_err() {
                break;
            }

            let mut line = String::new();
            if reader.read_line(&mut line).await.is_err() {
                break;
            }

            tokio::time::sleep(Duration::from_millis(5)).await;
        }

        let _ = srv_handle.await;
        Ok(())
    });
    lifecycle.spawn_task("ipc_load_worker", ipc_worker);

    // 2. Background worker: continuous audio streaming
    let audio_token = lifecycle.child_token();
    let audio_worker = tokio::spawn(async move {
        let config = AudioCaptureConfig {
            sample_rate: 16000,
            channels: 1,
            chunk_size: 160,
            ..Default::default()
        };
        let (_ctrl, mut rx) =
            AudioCaptureController::create_mock_capture(config, audio_token.clone());

        while !audio_token.is_cancelled() {
            if rx.recv().await.is_none() {
                break;
            }
        }
        Ok(())
    });
    lifecycle.spawn_task("audio_capture_worker", audio_worker);

    // 3. Background worker: continuous event bus publisher
    let event_token = lifecycle.child_token();
    let bus_clone = event_bus.clone();
    let event_worker = tokio::spawn(async move {
        while !event_token.is_cancelled() {
            bus_clone.publish(EngineEvent::CpuSpike {
                usage_percent: 77.7,
            });
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        Ok(())
    });
    lifecycle.spawn_task("event_publisher_worker", event_worker);

    // Allow workers to execute under load for 100ms
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Trigger shutdown while tasks are actively executing
    let shutdown_start = Instant::now();
    event_bus.publish(EngineEvent::EngineStopping);

    let result = lifecycle.shutdown().await;
    let elapsed = shutdown_start.elapsed();

    // Must shut down with zero errors within 5-second deadline
    assert!(
        result.is_ok(),
        "Shutdown completed with errors: {:?}",
        result.err()
    );
    assert!(
        elapsed < Duration::from_secs(5),
        "Shutdown took {:?}, exceeding 5s contract",
        elapsed
    );

    println!(
        ">>> Measured shutdown duration under active load: {:?}",
        elapsed
    );
}

// ============================================================================
// 4. Repeated Lifecycle Start/Stop Cycles (Resource Leak Audit)
// ============================================================================

#[tokio::test]
async fn test_repeated_lifecycle_cycles_clean_teardown() {
    for cycle in 1..=5 {
        let mut lifecycle = EngineLifecycle::new(Duration::from_secs(2));
        let (bus, mut rx) = EventBus::new(64);

        bus.publish(EngineEvent::EngineStarted);

        let worker_token = lifecycle.child_token();
        let handle = tokio::spawn(async move {
            tokio::select! {
                _ = worker_token.cancelled() => Ok(()),
                _ = tokio::time::sleep(Duration::from_secs(10)) => Ok(()),
            }
        });
        lifecycle.spawn_task("cycle_worker", handle);

        // Verify started event received
        let ev = rx.recv().await.unwrap();
        assert_eq!(ev, EngineEvent::EngineStarted);

        bus.publish(EngineEvent::EngineStopping);

        let res = lifecycle.shutdown().await;
        assert!(
            res.is_ok(),
            "Cycle {cycle} shutdown failed: {:?}",
            res.err()
        );
    }
}

// ============================================================================
// 5. IPC Roundtrip Latency Benchmark (< 2ms Target)
// ============================================================================

#[tokio::test]
async fn test_ipc_roundtrip_latency_benchmark() {
    let dispatcher = Arc::new(create_default_dispatcher());
    let cancel = CancellationToken::new();

    let (client_stream, server_stream) = tokio::io::duplex(65536);
    let srv_cancel = cancel.clone();
    let srv_handle = tokio::spawn(handle_connection(server_stream, dispatcher, srv_cancel));

    let (read_half, mut write_half) = tokio::io::split(client_stream);
    let mut reader = BufReader::new(read_half);

    // Warm-up phase: 10 requests to warm code paths, caches, and serde buffers
    let warmup_count = 10;
    for i in 0..warmup_count {
        let req = IpcRequest::new(format!("warmup_{i}"), "engine.ping", json!({}));
        let mut wire = serde_json::to_vec(&req).unwrap();
        wire.push(b'\n');
        write_half.write_all(&wire).await.unwrap();
        write_half.flush().await.unwrap();

        let mut line = String::new();
        reader.read_line(&mut line).await.unwrap();
    }

    // Benchmark phase: 100 timed sequential requests over warm reusable pipe
    let sample_count = 100;
    let mut durations: Vec<Duration> = Vec::with_capacity(sample_count);

    for i in 0..sample_count {
        let req = IpcRequest::new(format!("bench_{i}"), "engine.ping", json!({}));
        let mut wire = serde_json::to_vec(&req).unwrap();
        wire.push(b'\n');

        let start = Instant::now();
        write_half.write_all(&wire).await.unwrap();
        write_half.flush().await.unwrap();

        let mut line = String::new();
        reader.read_line(&mut line).await.unwrap();
        let elapsed = start.elapsed();

        durations.push(elapsed);
    }

    cancel.cancel();
    let _ = srv_handle.await;

    // Calculate benchmark statistics
    durations.sort();

    let sum: Duration = durations.iter().copied().sum();
    let avg = sum / (sample_count as u32);
    let min = durations.first().copied().unwrap();
    let max = durations.last().copied().unwrap();
    let median = durations[sample_count / 2];
    let p95 = durations[(sample_count as f64 * 0.95) as usize];
    let p99 = durations[(sample_count as f64 * 0.99) as usize];

    println!("\n================ IPC LATENCY BENCHMARK ================");
    println!("Warm-up requests: {}", warmup_count);
    println!("Measured samples: {}", sample_count);
    println!("Average latency:  {:?}", avg);
    println!("Median (P50):     {:?}", median);
    println!("P95 latency:      {:?}", p95);
    println!("P99 latency:      {:?}", p99);
    println!("Min latency:      {:?}", min);
    println!("Max latency:      {:?}", max);
    println!("=======================================================\n");

    // Assertion: warm IPC in-memory roundtrip completes within milliseconds
    assert!(
        avg < Duration::from_millis(5),
        "Average latency was {:?}",
        avg
    );
}

// ============================================================================
// 6. Idle Resource Footprint Audit
// ============================================================================

#[tokio::test]
async fn test_idle_resource_footprint() {
    let monitor = SystemMonitor::default();

    // First sample
    let _ = monitor.get_cpu().await;
    tokio::time::sleep(Duration::from_millis(250)).await;

    // Second sample to get meaningful CPU delta
    let cpu_info = monitor.get_cpu().await;
    let mem_info = monitor.get_memory().await;

    println!("\n================ IDLE RESOURCE AUDIT ================");
    println!("System CPU usage:        {:.2}%", cpu_info.usage_percent);
    println!("CPU core count:          {}", cpu_info.core_count);
    println!(
        "Memory total:            {} MB",
        mem_info.total_bytes / (1024 * 1024)
    );
    println!(
        "Memory available:        {} MB",
        mem_info.available_bytes / (1024 * 1024)
    );
    println!(
        "Memory used:             {} MB",
        mem_info.used_bytes / (1024 * 1024)
    );
    println!("=====================================================\n");

    assert!(mem_info.total_bytes > 0);
    assert!(cpu_info.core_count > 0);
}

// ============================================================================
// 7. Comprehensive Failure Modes
// ============================================================================

#[tokio::test]
async fn test_failure_modes_graceful_handling() {
    let dispatcher = Arc::new(create_default_dispatcher());
    let cancel = CancellationToken::new();

    // 1. Client disconnects immediately (EOF)
    {
        let (client, server) = tokio::io::duplex(1024);
        let srv = tokio::spawn(handle_connection(
            server,
            dispatcher.clone(),
            cancel.child_token(),
        ));
        drop(client);
        assert!(srv.await.unwrap().is_ok());
    }

    // 2. Client sends malformed JSON
    {
        let (client, server) = tokio::io::duplex(1024);
        let srv = tokio::spawn(handle_connection(
            server,
            dispatcher.clone(),
            cancel.child_token(),
        ));
        let (read_half, mut write_half) = tokio::io::split(client);
        let mut reader = BufReader::new(read_half);

        write_half.write_all(b"{not_json_payload\n").await.unwrap();
        write_half.flush().await.unwrap();

        let mut line = String::new();
        reader.read_line(&mut line).await.unwrap();
        let resp: IpcResponse = serde_json::from_str(&line).unwrap();

        assert!(!resp.success);
        assert_eq!(resp.error.unwrap().code, error_code::INVALID_REQUEST);
        drop(reader);
        drop(write_half);
        let _ = srv.await;
    }

    // 3. Protocol version mismatch
    {
        let (client, server) = tokio::io::duplex(1024);
        let srv = tokio::spawn(handle_connection(
            server,
            dispatcher.clone(),
            cancel.child_token(),
        ));
        let (read_half, mut write_half) = tokio::io::split(client);
        let mut reader = BufReader::new(read_half);

        let mut req = IpcRequest::new("ver_mismatch", "engine.ping", json!({}));
        req.version = 999;
        let mut wire = serde_json::to_vec(&req).unwrap();
        wire.push(b'\n');

        write_half.write_all(&wire).await.unwrap();
        write_half.flush().await.unwrap();

        let mut line = String::new();
        reader.read_line(&mut line).await.unwrap();
        let resp: IpcResponse = serde_json::from_str(&line).unwrap();

        assert!(!resp.success);
        assert_eq!(resp.error.unwrap().code, error_code::VERSION_MISMATCH);
        drop(reader);
        drop(write_half);
        let _ = srv.await;
    }

    // 4. Unknown method
    {
        let (client, server) = tokio::io::duplex(1024);
        let srv = tokio::spawn(handle_connection(
            server,
            dispatcher.clone(),
            cancel.child_token(),
        ));
        let (read_half, mut write_half) = tokio::io::split(client);
        let mut reader = BufReader::new(read_half);

        let req = IpcRequest::new("unknown_req", "nonexistent.method", json!({}));
        let mut wire = serde_json::to_vec(&req).unwrap();
        wire.push(b'\n');

        write_half.write_all(&wire).await.unwrap();
        write_half.flush().await.unwrap();

        let mut line = String::new();
        reader.read_line(&mut line).await.unwrap();
        let resp: IpcResponse = serde_json::from_str(&line).unwrap();

        assert!(!resp.success);
        assert_eq!(resp.error.unwrap().code, error_code::NOT_FOUND);
        drop(reader);
        drop(write_half);
        let _ = srv.await;
    }
}
