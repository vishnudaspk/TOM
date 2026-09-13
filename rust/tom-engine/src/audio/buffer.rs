use std::collections::VecDeque;

/// A bounded FIFO buffer for raw floating-point PCM audio samples.
#[derive(Debug, Clone)]
pub struct AudioBuffer {
    data: VecDeque<f32>,
    capacity: usize,
}

impl AudioBuffer {
    /// Creates a new `AudioBuffer` with a fixed maximum sample capacity.
    pub fn new(capacity: usize) -> Self {
        Self {
            data: VecDeque::with_capacity(capacity),
            capacity,
        }
    }

    /// Pushes a slice of audio samples into the buffer.
    ///
    /// If the incoming samples exceed available capacity, the oldest samples
    /// are dropped to prevent unbounded memory growth (ring-buffer semantics).
    /// Returns the number of samples successfully written.
    pub fn push_samples(&mut self, samples: &[f32]) -> usize {
        let count = samples.len();
        if count == 0 {
            return 0;
        }

        // If incoming slice is larger than capacity, only keep the tail
        let slice_to_keep = if count > self.capacity {
            &samples[count - self.capacity..]
        } else {
            samples
        };

        // Evict oldest samples if adding would exceed capacity
        let needed_space = slice_to_keep.len();
        let available_space = self.capacity.saturating_sub(self.data.len());
        if needed_space > available_space {
            let to_evict = needed_space - available_space;
            self.data.drain(0..to_evict);
        }

        self.data.extend(slice_to_keep.iter().copied());
        count
    }

    /// Pops up to `max_samples` from the front of the buffer.
    pub fn pop_samples(&mut self, max_samples: usize) -> Vec<f32> {
        let count = max_samples.min(self.data.len());
        self.data.drain(0..count).collect()
    }

    /// Peeks up to `max_samples` from the front without removing them.
    pub fn peek_samples(&self, max_samples: usize) -> Vec<f32> {
        let count = max_samples.min(self.data.len());
        self.data.iter().take(count).copied().collect()
    }

    /// Returns the current number of samples in the buffer.
    pub fn len(&self) -> usize {
        self.data.len()
    }

    /// Returns true if the buffer contains no samples.
    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    /// Returns the maximum capacity of the buffer.
    pub fn capacity(&self) -> usize {
        self.capacity
    }

    /// Clears all samples from the buffer.
    pub fn clear(&mut self) {
        self.data.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_audio_buffer_push_and_pop() {
        let mut buf = AudioBuffer::new(10);
        assert_eq!(buf.len(), 0);
        assert!(buf.is_empty());
        assert_eq!(buf.capacity(), 10);

        let input = vec![0.1, 0.2, 0.3, 0.4];
        let written = buf.push_samples(&input);
        assert_eq!(written, 4);
        assert_eq!(buf.len(), 4);

        let popped = buf.pop_samples(2);
        assert_eq!(popped, vec![0.1, 0.2]);
        assert_eq!(buf.len(), 2);

        let remaining = buf.pop_samples(5);
        assert_eq!(remaining, vec![0.3, 0.4]);
        assert!(buf.is_empty());
    }

    #[test]
    fn test_audio_buffer_overflow_drops_oldest() {
        let mut buf = AudioBuffer::new(4);
        buf.push_samples(&[1.0, 2.0, 3.0]);
        assert_eq!(buf.len(), 3);

        // Push 3 more samples into a buffer of capacity 4
        buf.push_samples(&[4.0, 5.0, 6.0]);
        assert_eq!(buf.len(), 4);

        // Oldest samples 1.0 and 2.0 should have been evicted
        let samples = buf.pop_samples(4);
        assert_eq!(samples, vec![3.0, 4.0, 5.0, 6.0]);
    }

    #[test]
    fn test_audio_buffer_peek_and_clear() {
        let mut buf = AudioBuffer::new(5);
        buf.push_samples(&[0.5, -0.5, 0.25]);

        let peeked = buf.peek_samples(2);
        assert_eq!(peeked, vec![0.5, -0.5]);
        assert_eq!(buf.len(), 3); // Length unchanged after peek

        buf.clear();
        assert_eq!(buf.len(), 0);
        assert!(buf.is_empty());
    }
}
