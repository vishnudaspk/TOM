pub mod dispatcher;
pub mod handlers;
pub mod protocol;
pub mod server;

pub use dispatcher::IpcDispatcher;
pub use handlers::create_default_dispatcher;
pub use protocol::{error_code, IpcError, IpcRequest, IpcResponse, CURRENT_PROTOCOL_VERSION};
pub use server::{handle_connection, IpcTransportError, MAX_FRAME_BYTES, PIPE_NAME};
