import { createClient, enc } from "../shared/api";

export const UNAUTHORIZED_EVENT = "trove:unauthorized";

/** Owner workspace client. A 401 anywhere drops the app back to the sign-in screen. */
export const api = createClient("", () => window.dispatchEvent(new Event(UNAUTHORIZED_EVENT)), ["/auth/status"]);

export const casePath = (caseId: string, suffix = "") => `/cases/${enc(caseId)}${suffix}`;
