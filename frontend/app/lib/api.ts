/**
 * Centralized API base URL.
 *
 * Uses the NEXT_PUBLIC_API_URL environment variable when set (e.g. in
 * docker-compose) and falls back to localhost:8000 for local development.
 */
export const API =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
