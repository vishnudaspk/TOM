use tokio::sync::broadcast;
use tracing::debug;

use crate::events::types::EngineEvent;

/// Default capacity for the broadcast event bus ring buffer.
pub const DEFAULT_EVENT_BUS_CAPACITY: usize = 256;

/// Asynchronous fan-out event bus backed by `tokio::sync::broadcast`.
#[derive(Clone, Debug)]
pub struct EventBus {
    sender: broadcast::Sender<EngineEvent>,
}

impl EventBus {
    /// Creates a new `EventBus` with the specified ring buffer capacity.
    /// Returns the bus handle along with an initial subscriber receiver.
    pub fn new(capacity: usize) -> (Self, broadcast::Receiver<EngineEvent>) {
        let (sender, receiver) = broadcast::channel(capacity);
        (Self { sender }, receiver)
    }

    /// Publishes an event to all active subscribers.
    ///
    /// This method is non-blocking and never panics. If there are no active
    /// subscribers, the event is cleanly dropped and logged at `debug` level.
    pub fn publish(&self, event: EngineEvent) {
        if let Err(err) = self.sender.send(event) {
            // Expected normal condition when no subscribers are listening
            debug!(error = %err, "Event published with no active subscribers");
        }
    }

    /// Subscribes to the event bus, returning a new `broadcast::Receiver`.
    pub fn subscribe(&self) -> broadcast::Receiver<EngineEvent> {
        self.sender.subscribe()
    }

    /// Returns the number of currently active subscribers.
    pub fn subscriber_count(&self) -> usize {
        self.sender.receiver_count()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_single_event_delivery() {
        let (bus, mut rx) = EventBus::new(16);
        bus.publish(EngineEvent::EngineStarted);

        let received = rx.recv().await.expect("failed to receive event");
        assert_eq!(received, EngineEvent::EngineStarted);
    }

    #[tokio::test]
    async fn test_multiple_event_ordering_and_count() {
        let (bus, mut rx) = EventBus::new(16);
        let events = vec![
            EngineEvent::EngineStarted,
            EngineEvent::CpuSpike {
                usage_percent: 92.5,
            },
            EngineEvent::MemoryPressure {
                used_bytes: 14_000_000_000,
                total_bytes: 16_000_000_000,
            },
            EngineEvent::IpcClientConnected,
            EngineEvent::IpcClientDisconnected,
            EngineEvent::EngineStopping,
        ];

        for ev in &events {
            bus.publish(ev.clone());
        }

        for expected in events {
            let received = rx.recv().await.expect("failed to receive event");
            assert_eq!(received, expected);
        }
    }

    #[tokio::test]
    async fn test_publish_with_no_subscribers_does_not_panic() {
        let (bus, rx) = EventBus::new(16);
        // Explicitly drop all subscribers
        drop(rx);
        assert_eq!(bus.subscriber_count(), 0);

        // Publishing must not panic or fail
        bus.publish(EngineEvent::EngineStarted);
        bus.publish(EngineEvent::CpuSpike {
            usage_percent: 99.0,
        });

        // A new subscriber will only receive subsequent events
        let mut new_rx = bus.subscribe();
        assert_eq!(bus.subscriber_count(), 1);

        bus.publish(EngineEvent::EngineStopping);
        let received = new_rx.recv().await.expect("failed to receive event");
        assert_eq!(received, EngineEvent::EngineStopping);
    }

    #[tokio::test]
    async fn test_slow_subscriber_lagged_receiver_handling() {
        // Small capacity to trigger lag overflow intentionally
        let (bus, mut rx) = EventBus::new(2);

        // Publish 4 events, exceeding capacity of 2
        bus.publish(EngineEvent::EngineStarted);
        bus.publish(EngineEvent::CpuSpike {
            usage_percent: 50.0,
        });
        bus.publish(EngineEvent::CpuSpike {
            usage_percent: 75.0,
        });
        bus.publish(EngineEvent::CpuSpike {
            usage_percent: 100.0,
        });

        // First recv on lagged subscriber should return Lagged error with skipped count
        match rx.recv().await {
            Err(broadcast::error::RecvError::Lagged(skipped)) => {
                assert!(
                    skipped >= 2,
                    "Expected at least 2 events skipped, got {skipped}"
                );
            }
            other => panic!("Expected Lagged error, got {other:?}"),
        }

        // Subsequent recv should recover and yield the oldest retained event in the buffer
        let next_event = rx.recv().await.expect("Expected retained event after lag");
        assert_eq!(
            next_event,
            EngineEvent::CpuSpike {
                usage_percent: 75.0
            }
        );

        let final_event = rx.recv().await.expect("Expected final event");
        assert_eq!(
            final_event,
            EngineEvent::CpuSpike {
                usage_percent: 100.0
            }
        );
    }
}
