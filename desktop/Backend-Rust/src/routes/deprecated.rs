// Deprecated API routes — return 410 Gone for endpoints no longer served by the Rust backend.
//
// These endpoints had ZERO traffic over 7 days (Apr 18-25, 2026) and the current
// desktop Swift app routes all data CRUD to the Python backend (OMI_PYTHON_API_URL).
//
// Clients should migrate to https://api.omi.me (the Python backend).

use axum::{
    extract::Request,
    http::StatusCode,
    response::{IntoResponse, Json, Response},
    routing::{any, delete, get, patch, post},
    Router,
};
use serde_json::json;

use crate::AppState;

/// Standard 410 Gone response for deprecated endpoints.
async fn deprecated_handler(req: Request) -> Response {
    let path = req.uri().path().to_string();
    let method = req.method().to_string();
    tracing::warn!("Deprecated endpoint called: {} {} — returning 410 Gone", method, path);
    (
        StatusCode::GONE,
        Json(json!({
            "error": "gone",
            "message": format!("This endpoint ({} {}) is deprecated and no longer served by the desktop backend. Use https://api.omi.me{} instead.", method, path, path),
            "migration": "https://api.omi.me"
        })),
    )
        .into_response()
}

/// Register all deprecated routes so they return 410 instead of 404.
/// This lets old clients get a clear deprecation message rather than a confusing 404.
pub fn deprecated_routes() -> Router<AppState> {
    Router::new()
        // ── Chat context & generation (0 traffic, Swift uses Python) ──────────
        .route("/v2/chat-context", post(deprecated_handler))
        .route("/v2/chat/initial-message", post(deprecated_handler))
        .route("/v2/chat/generate-title", post(deprecated_handler))
        // ── Chat sessions (0 traffic) ─────────────────────────────────────────
        .route("/v2/chat-sessions", get(deprecated_handler).post(deprecated_handler))
        .route(
            "/v2/chat-sessions/{id}",
            get(deprecated_handler)
                .patch(deprecated_handler)
                .delete(deprecated_handler),
        )
        // ── Advice (0 traffic) ────────────────────────────────────────────────
        .route("/v1/advice", get(deprecated_handler).post(deprecated_handler))
        .route(
            "/v1/advice/{id}",
            patch(deprecated_handler).delete(deprecated_handler),
        )
        .route("/v1/advice/mark-all-read", post(deprecated_handler))
        // ── Focus sessions (0 traffic) ────────────────────────────────────────
        .route(
            "/v1/focus-sessions",
            get(deprecated_handler).post(deprecated_handler),
        )
        .route("/v1/focus-sessions/{id}", delete(deprecated_handler))
        .route("/v1/focus-stats", get(deprecated_handler))
        // ── Folders (0 traffic) ───────────────────────────────────────────────
        .route(
            "/v1/folders",
            get(deprecated_handler).post(deprecated_handler),
        )
        .route(
            "/v1/folders/{id}",
            patch(deprecated_handler).delete(deprecated_handler),
        )
        .route("/v1/folders/reorder", post(deprecated_handler))
        .route(
            "/v1/folders/{id}/conversations/bulk-move",
            post(deprecated_handler),
        )
        // ── Goals (0 traffic) ─────────────────────────────────────────────────
        .route("/v1/goals", post(deprecated_handler))
        .route("/v1/goals/all", get(deprecated_handler))
        .route("/v1/goals/completed", get(deprecated_handler))
        .route(
            "/v1/goals/{id}",
            patch(deprecated_handler).delete(deprecated_handler),
        )
        .route("/v1/goals/{id}/progress", patch(deprecated_handler))
        .route("/v1/goals/{id}/history", get(deprecated_handler))
        // ── Daily score (0 traffic) ───────────────────────────────────────────
        .route("/v1/daily-score", get(deprecated_handler))
        .route("/v1/scores", get(deprecated_handler))
        // ── People (0 traffic) ────────────────────────────────────────────────
        .route(
            "/v1/users/people",
            get(deprecated_handler).post(deprecated_handler),
        )
        .route("/v1/users/people/{person_id}", delete(deprecated_handler))
        .route(
            "/v1/users/people/{person_id}/name",
            patch(deprecated_handler),
        )
        // ── Personas (0 traffic) ──────────────────────────────────────────────
        .route(
            "/v1/personas",
            get(deprecated_handler)
                .post(deprecated_handler)
                .patch(deprecated_handler)
                .delete(deprecated_handler),
        )
        .route("/v1/personas/generate-prompt", post(deprecated_handler))
        .route("/v1/personas/check-username", get(deprecated_handler))
        // ── Knowledge graph (0 traffic) ───────────────────────────────────────
        .route(
            "/v1/knowledge-graph",
            get(deprecated_handler).delete(deprecated_handler),
        )
        .route("/v1/knowledge-graph/rebuild", post(deprecated_handler))
        // ── LLM usage (0 traffic) ─────────────────────────────────────────────
        .route("/v1/users/me/llm-usage", post(deprecated_handler))
        .route("/v1/users/me/llm-usage/total", get(deprecated_handler))
        // ── Stats (0 traffic) ─────────────────────────────────────────────────
        .route("/v1/users/stats/chat-messages", get(deprecated_handler))
        // ── Apps (0 traffic) ──────────────────────────────────────────────────
        .route("/v1/apps", get(deprecated_handler))
        .route("/v1/approved-apps", get(deprecated_handler))
        .route("/v1/apps/popular", get(deprecated_handler))
        .route("/v1/apps/{app_id}", get(deprecated_handler))
        .route("/v1/apps/{app_id}/reviews", get(deprecated_handler))
        .route("/v2/apps", get(deprecated_handler))
        .route("/v2/apps/search", get(deprecated_handler))
        .route("/v1/apps/enable", post(deprecated_handler))
        .route("/v1/apps/disable", post(deprecated_handler))
        .route("/v1/apps/enabled", get(deprecated_handler))
        .route("/v1/apps/review", post(deprecated_handler))
        .route("/v1/app-categories", get(deprecated_handler))
        .route("/v1/app-capabilities", get(deprecated_handler))
        // ── Auth (0 traffic — handled by Auth-Python service) ─────────────────
        // Note: Auth-Python (desktop-auth) handles OAuth via OMI_AUTH_URL.
        // These Rust auth routes had 0 traffic in 7 days.
        // Apple domain association kept because it may be hit by Apple verification.
}
