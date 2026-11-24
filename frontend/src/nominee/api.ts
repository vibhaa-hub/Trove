import { createClient } from "../shared/api";

export const NOMINEE_UNAUTHORIZED = "trove:nominee-unauthorized";

export const api = createClient("/api/nominee", () => window.dispatchEvent(new Event(NOMINEE_UNAUTHORIZED)), ["/me", "/outbox"]);
