// Routes module

// ── Active routes (have real traffic) ─────────────────────────────────────────
pub mod agent;
pub mod auth;
pub mod chat_completions;
pub mod config;
pub mod crisp;
pub mod health;
pub mod proxy;
pub mod rate_limit;
pub mod screen_activity;
pub mod tts;
pub mod updates;
pub mod webhooks;

// ── Legacy routes (declining traffic from old clients, kept functional, pending removal) ──
pub mod action_items;
pub mod conversations;
pub mod memories;
pub mod messages;
pub mod staged_tasks;
pub mod users;

// ── Deprecated route stubs (0 traffic — return 410 Gone) ─────────────────────
pub mod deprecated;

// ── Deprecated modules (kept as source files, no longer registered in router) ─
// These had 0 traffic in 7 days (Apr 18-25, 2026). The current desktop app
// routes all these to the Python backend (OMI_PYTHON_API_URL = api.omi.me).
// Source files retained for reference; deprecated.rs returns 410 for their paths.
pub mod advice;
pub mod apps;
pub mod chat;
pub mod chat_sessions;
pub mod daily_score;
pub mod focus_sessions;
pub mod folders;
pub mod goals;
pub mod knowledge_graph;
pub mod llm_usage;
pub mod people;
pub mod personas;
pub mod stats;

// ── Active re-exports ─────────────────────────────────────────────────────────
pub use agent::agent_routes;
pub use auth::auth_routes;
pub use chat_completions::chat_completions_routes;
pub use config::config_routes;
pub use crisp::crisp_routes;
pub use deprecated::deprecated_routes;
pub use health::health_routes;
pub use proxy::proxy_routes;
pub use screen_activity::screen_activity_routes;
pub use tts::tts_routes;
pub use updates::updates_routes;
pub use webhooks::webhook_routes;

// ── Legacy re-exports (declining traffic, kept functional, pending removal) ─────
pub use action_items::action_items_routes;
pub use conversations::conversations_routes;
pub use memories::memories_routes;
pub use messages::messages_routes;
pub use staged_tasks::staged_tasks_routes;
pub use users::users_routes;
