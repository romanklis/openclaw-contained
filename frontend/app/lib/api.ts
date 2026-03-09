/**
 * Centralized API base URL.
 *
 * Uses the NEXT_PUBLIC_API_URL environment variable when set (e.g. in
 * docker-compose) and falls back to localhost:8000 for local development.
 */
export const API =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * API Gateway base URL.
 *
 * The gateway handles OpenAI-compatible endpoints (/v1/models,
 * /v1/chat/completions) and agent profile resolution.
 */
export const API_GATEWAY =
  process.env.NEXT_PUBLIC_API_GATEWAY_URL || "http://localhost:8080";
