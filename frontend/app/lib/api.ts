/**
 * Centralized API base URL.
 *
 * Defaults to the same origin so the Next.js server can proxy /api/* to the
 * control plane. Set NEXT_PUBLIC_API_URL only when a direct cross-origin API
 * URL is explicitly required.
 */
export const API = process.env.NEXT_PUBLIC_API_URL || "";

/**
 * API Gateway base URL.
 *
 * The gateway handles OpenAI-compatible endpoints (/v1/models,
 * /v1/chat/completions) and agent profile resolution.
 */
export const API_GATEWAY =
  process.env.NEXT_PUBLIC_API_GATEWAY_URL || "http://localhost:8080";

/**
 * Temporal UI base URL for workflow deep links.
 */
export const TEMPORAL_UI =
  process.env.NEXT_PUBLIC_TEMPORAL_UI_URL || "http://localhost:8088";
