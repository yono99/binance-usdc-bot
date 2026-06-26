//! IPC core <-> Python (pure-Rust ZeroMQ, tanpa libzmq native).
//! Socket dipisah agar tiap task memiliki kepemilikan sendiri:
//!   MarketPub  : PUB  candle ternormalisasi  -> Python
//!   EventPub   : PUB  fill/close/reject       -> Python
//!   SignalPull : PULL intent dari Python      -> core
use tracing::{error, warn};
use zeromq::{PubSocket, PullSocket, Socket, SocketRecv, SocketSend, ZmqMessage};

use crate::types::{Candle, OrderEvent, SignalIntent};

pub struct MarketPub(PubSocket);
pub struct EventPub(PubSocket);
pub struct SignalPull(PullSocket);

impl MarketPub {
    pub async fn bind(addr: &str) -> anyhow::Result<Self> {
        let mut s = PubSocket::new();
        s.bind(addr).await?;
        Ok(Self(s))
    }
    pub async fn publish(&mut self, c: &Candle) {
        if let Ok(s) = serde_json::to_string(c) {
            if let Err(e) = self.0.send(ZmqMessage::from(s)).await {
                warn!("publish candle gagal: {e}");
            }
        }
    }
}

impl EventPub {
    pub async fn bind(addr: &str) -> anyhow::Result<Self> {
        let mut s = PubSocket::new();
        s.bind(addr).await?;
        Ok(Self(s))
    }
    pub async fn publish(&mut self, ev: &OrderEvent) {
        if let Ok(s) = serde_json::to_string(ev) {
            if let Err(e) = self.0.send(ZmqMessage::from(s)).await {
                warn!("publish event gagal: {e}");
            }
        }
    }
}

impl SignalPull {
    pub async fn bind(addr: &str) -> anyhow::Result<Self> {
        let mut s = PullSocket::new();
        s.bind(addr).await?;
        Ok(Self(s))
    }
    /// Blok sampai satu intent valid diterima dari Python.
    pub async fn recv(&mut self) -> Option<SignalIntent> {
        match self.0.recv().await {
            Ok(msg) => {
                let bytes = msg.get(0).map(|b| b.as_ref()).unwrap_or(&[]);
                match serde_json::from_slice::<SignalIntent>(bytes) {
                    Ok(intent) => Some(intent),
                    Err(e) => {
                        error!("intent tidak valid: {e}");
                        None
                    }
                }
            }
            Err(e) => {
                error!("recv intent gagal: {e}");
                None
            }
        }
    }
}
