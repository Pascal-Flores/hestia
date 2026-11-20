mod api;
mod engine;
mod model;
mod scheduler;
mod speech;
mod state;

use std::{net::SocketAddr, path::PathBuf, sync::Arc};
use anyhow::Result;
use tracing::info;
use tracing_subscriber::EnvFilter;
use engine::Engine;
use state::AppState;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .init();

    let tools = PathBuf::from(std::env::var("HESTIA_TOOLS_DIR").unwrap_or_else(|_| "/app/tools".into()));
    let data = PathBuf::from(std::env::var("HESTIA_DATA_DIR").unwrap_or_else(|_| "/data".into()));
    std::fs::create_dir_all(&data)?;
    let core = Arc::new(Engine::new(tools.clone(), data.join("embeddings"))?);
    let state = AppState::load(core, data)?;
    let state = Arc::new(state);
    state.reload_index().await?;
    scheduler::spawn_scheduler(state.clone());
    let app = api::routes(state);
    let bind = std::env::var("HESTIA_BIND").unwrap_or_else(|_| "0.0.0.0:8765".into());
    let addr: SocketAddr = bind.parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    info!("Hestia Rust core listening on {} ({} tools)", addr, tools.display());
    axum::serve(listener, app).await?;
    Ok(())
}
