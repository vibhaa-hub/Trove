import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { NomineeApp } from "../nominee/NomineeApp";
import { OwnerApp } from "../owner/OwnerApp";
import { ReviewerApp } from "../reviewer/ReviewerApp";
import { axeViolations, mockFetch, renderApp } from "./helpers";

const CASE = { id: "case-1", mode: "plan", subject_name: "Asha Rao", jurisdiction: "India" };
const WORKFLOW = {
  stage: "asset_verification",
  mode: "plan",
  asset_count: 2,
  assets_needing_review: 1,
  death_status: "not_submitted",
  unverified_beneficiaries: 0,
  can_advance: false,
};
const ASSETS = {
  assets: [
    { id: "a1", asset_type: "bank_account", status: "needs_review", institution: "HDFC Bank", masked_reference: "XX5566" },
    { id: "a2", asset_type: "insurance_policy", status: "confirmed", institution: "LIC" },
  ],
};
const LEGACY = {
  policy: { quorum: 2, waiting_period_hours: 72, checkin_interval_days: 90, last_owner_checkin: null },
  nominees: [
    {
      id: "n1",
      full_name: "Ravi Rao",
      relationship: "son",
      role: "primary",
      email: "r***@example.test",
      phone: "+91******5678",
      status: "invited",
      verified: false,
      identity_status: "not_submitted",
      allowed_categories: ["will"],
      steps: [
        { key: "invite", label: "Invite accepted", done: true },
        { key: "email", label: "Email verified", done: false },
        { key: "phone", label: "Phone verified", done: false },
      ],
    },
  ],
  requests: [],
  outbox: [],
  verified_nominees: 0,
  document_categories: ["will"],
};

function signedInOwner() {
  return mockFetch([
    ["GET", /\/auth\/status$/, 200, { authenticated: true, setup_required: false, signup_allowed: false, user: { id: "u1", email: "owner@example.test" } }],
    ["GET", /\/cases$/, 200, { cases: [CASE] }],
    ["GET", /\/workflow$/, 200, WORKFLOW],
    ["GET", /\/assets$/, 200, ASSETS],
    ["GET", /\/legacy$/, 200, LEGACY],
    ["GET", /\/legacy\/audit$/, 200, { chain: { valid: true, events: 0 }, events: [] }],
    ["PATCH", /\/assets\/a1$/, 200, { id: "a1", status: "confirmed" }],
  ]);
}

describe("owner workspace", () => {
  it("shows an accessible first-run setup screen", async () => {
    mockFetch([["GET", /\/auth\/status$/, 200, { authenticated: false, setup_required: true, signup_allowed: false }]]);
    renderApp(<OwnerApp />);
    expect(await screen.findByRole("heading", { level: 1, name: "Secure your workspace" })).toBeInTheDocument();
    expect(screen.getByLabelText(/^Password/)).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
    expect(await axeViolations()).toEqual([]);
  });

  it("password checklist reports requirements as they are met", async () => {
    mockFetch([["GET", /\/auth\/status$/, 200, { authenticated: false, setup_required: true, signup_allowed: false }]]);
    renderApp(<OwnerApp />);
    const password = await screen.findByLabelText(/^Password/);
    await userEvent.type(password, "Correct-Horse-9x");
    expect(screen.getByText(/At least 12 characters/)).toHaveTextContent("(met)");
    await userEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
  });

  it("renders the overview with landmarks, current-page nav and no axe violations", async () => {
    signedInOwner();
    renderApp(<OwnerApp />);
    expect(await screen.findByRole("heading", { level: 1, name: "Case overview" })).toBeInTheDocument();
    expect(await screen.findByText("HDFC Bank · XX5566")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Case sections" });
    expect(within(nav).getByRole("link", { name: /Overview/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(await axeViolations()).toEqual([]);
  });

  it("confirming an asset lead calls the API and announces the result", async () => {
    const calls = signedInOwner();
    renderApp(<OwnerApp />);
    await userEvent.click(await screen.findByRole("button", { name: "Confirm Bank account at HDFC Bank" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.endsWith("/assets/a1"))).toBe(true));
    await waitFor(() => expect(screen.getByRole("status", { name: "" })).toBeTruthy());
    expect(await screen.findByText(/Bank account at HDFC Bank: confirmed/)).toBeInTheDocument();
  });

  it("plan section renders nominees with a labelled stepper", async () => {
    signedInOwner();
    window.location.hash = "#/plan";
    renderApp(<OwnerApp />);
    expect(await screen.findByRole("heading", { level: 1, name: "Plan · Legacy vault" })).toBeInTheDocument();
    const steps = await screen.findByRole("list", { name: "Verification progress for Ravi Rao" });
    expect(within(steps).getAllByRole("listitem")[1]).toHaveAttribute("aria-current", "step");
    expect(await axeViolations()).toEqual([]);
  });
});

describe("nominee portal", () => {
  it("signed-out view uses keyboard-operable tabs and passes axe", async () => {
    mockFetch([["GET", /\/api\/nominee\/me$/, 401, { error: "Sign in" }]]);
    renderApp(<NomineeApp />);
    const tab = await screen.findByRole("tab", { name: "I have an invite code" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    tab.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Sign in" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Sign in" })).toHaveFocus();
    expect(screen.getByLabelText(/Authenticator code/)).toHaveAttribute("autocomplete", "one-time-code");
    expect(await axeViolations()).toEqual([]);
  });
});

describe("reviewer console", () => {
  it("sign-in form passes axe", async () => {
    mockFetch([["GET", /\/api\/reviewer\/queue$/, 401, { error: "Sign in" }]]);
    renderApp(<ReviewerApp />);
    expect(await screen.findByRole("heading", { level: 1, name: "Reviewer sign-in" })).toBeInTheDocument();
    expect(await axeViolations()).toEqual([]);
  });
});

describe("axe harness", () => {
  it("does flag real violations (negative control)", async () => {
    document.body.innerHTML = '<main><input type="text"><img src="x.png"></main>';
    const violations = await axeViolations();
    expect(violations.some((v) => v.startsWith("label"))).toBe(true);
    expect(violations.some((v) => v.startsWith("image-alt"))).toBe(true);
    document.body.innerHTML = "";
  });
});
