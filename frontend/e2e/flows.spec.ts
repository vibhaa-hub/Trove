/**
 * Full user journeys in a real browser against the real server:
 *   1. Plan: owner sets up a vault -> nominee enrols, verifies, proves identity ->
 *      death claim -> certificate review -> release -> nominee downloads the will.
 *   2. Discovery: start from a phone and a statement -> leads -> promote -> confirm ->
 *      knowledge graph -> assistant.
 */
import { expect, test, type Browser, type Page } from "@playwright/test";
import { devInboxCode, expectAccessible, expectToast, file, freshTotp } from "./helpers";

test.describe.configure({ mode: "serial" });

const OWNER = { email: "asha@example.test", password: "E2e-Owner-Pass-2026" };
const NOMINEE = { name: "Ravi Rao", email: "ravi@example.test", phone: "+919811112222", last4: "1111", dob: "1994-02-11", password: "E2e-Nominee-Pass-2026" };
const WILL = "LAST WILL OF ASHA RAO (TEST). Everything to Ravi Rao.";

async function ownerSignIn(page: Page) {
  await page.goto("/");
  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading).toHaveText(/Secure your workspace|Sign in to Trove/);
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password", { exact: false }).first().fill(OWNER.password);
  await page.getByRole("button", { name: /Create password|Sign in/ }).click();
  await expect(page.getByRole("navigation", { name: "Case sections" })).toBeVisible();
}

async function newCase(page: Page, mode: "Plan" | "Discovery", name: string) {
  await page.getByRole("button", { name: "+ New case" }).click();
  const dialog = page.getByRole("dialog", { name: "Start a case" });
  await dialog.getByRole("radio", { name: new RegExp(`^${mode}`) }).check();
  await dialog.getByLabel("Whose estate is this?").fill(name);
  await dialog.getByLabel("Country whose law applies").selectOption("IN");
  await dialog.getByLabel("State / region").fill("Maharashtra");
  await dialog.getByRole("button", { name: "Create case" }).click();
  await expectToast(page, `Case for ${name} created.`);
}

async function nav(page: Page, section: string) {
  await page.getByRole("navigation", { name: "Case sections" }).getByRole("link", { name: section }).click();
}

async function nomineePage(browser: Browser, baseURL: string) {
  const context = await browser.newContext({ baseURL });
  return context.newPage();
}

test("Plan: legacy vault from setup to verified release", async ({ page, browser, baseURL }) => {
  await test.step("owner creates the workspace", async () => {
    await ownerSignIn(page);
    await expectAccessible(page, "owner overview (empty)");
  });

  await test.step("owner starts a Plan case and sets the release policy", async () => {
    await newCase(page, "Plan", "Asha Rao");
    await expect(page.getByRole("heading", { level: 1, name: "Plan · Legacy vault" })).toBeVisible();
    await page.getByLabel("Confirmations needed").fill("1");
    await page.getByLabel("Cooling-off (hours)").fill("0");
    await page.getByRole("button", { name: "Save policy" }).click();
    await expectToast(page, "Release policy saved.");
  });

  let invite = "";
  await test.step("owner adds a nominee and gets a one-time invite code", async () => {
    await page.getByRole("button", { name: "+ Add nominee" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a nominee or beneficiary" });
    await dialog.getByLabel("Full name").fill(NOMINEE.name);
    await dialog.getByLabel("Relationship").fill("son");
    await dialog.getByLabel("Email they will verify").fill(NOMINEE.email);
    await dialog.getByLabel("Phone they will verify").fill(NOMINEE.phone);
    await dialog.getByLabel("Last 4 characters of their ID").fill(NOMINEE.last4);
    await dialog.getByLabel("Their date of birth").fill(NOMINEE.dob);
    await dialog.getByRole("button", { name: "Add and create invite code" }).click();
    const inviteDialog = page.getByRole("dialog", { name: `Invite code for ${NOMINEE.name}` });
    invite = (await inviteDialog.locator("code.secret").textContent())!.trim();
    expect(invite).toMatch(/^[A-Z0-9-]{10,}$/);
    await expectAccessible(page, "invite code dialog");
    await inviteDialog.getByRole("button", { name: "I've saved it" }).click();
  });

  await test.step("owner uploads the will to the encrypted vault", async () => {
    await nav(page, "Documents");
    await page.getByLabel("Document type").selectOption("will");
    await page.getByLabel(/^File/).setInputFiles(file("asha-will.txt", WILL));
    await page.getByRole("button", { name: "Upload document" }).click();
    await expectToast(page, /Encrypted and saved/);
    await expect(page.locator(".item-title", { hasText: "asha-will.txt" })).toBeVisible();
    await expect(page.getByText("Selected: asha-will.txt")).toHaveCount(0); // file field cleared after upload
  });

  const nominee = await nomineePage(browser, baseURL!);
  let secret = "";
  await test.step("nominee accepts the invitation", async () => {
    await nominee.goto("/nominee");
    await expectAccessible(nominee, "nominee sign-in");
    await nominee.getByRole("textbox", { name: "Invite code" }).fill(invite);
    await nominee.getByLabel("Your email").fill(NOMINEE.email);
    await nominee.getByLabel("Choose a password").fill(NOMINEE.password);
    await nominee.getByRole("button", { name: "Accept invitation" }).click();
    await expect(nominee.getByRole("heading", { name: "Confirm your email" })).toBeVisible();
  });

  await test.step("nominee verifies email and phone with one-time codes", async () => {
    await nominee.getByLabel("Verification code").fill(await devInboxCode(nominee, "Email"));
    await nominee.getByRole("button", { name: "Verify email" }).click();
    await expect(nominee.getByRole("heading", { name: "Confirm your phone" })).toBeVisible();
    await nominee.getByLabel("Verification code").fill(await devInboxCode(nominee, "Phone"));
    await nominee.getByRole("button", { name: "Verify phone" }).click();
  });

  await test.step("nominee turns on authenticator-app MFA", async () => {
    await nominee.getByRole("button", { name: "Set up an authenticator app" }).click();
    secret = (await nominee.locator("code.secret").first().textContent())!.replace(/\s/g, "");
    await nominee.getByLabel("6-digit code from the app").fill(await freshTotp(secret));
    await nominee.getByRole("button", { name: "Turn on two-factor authentication" }).click();
    await expect(nominee.getByRole("heading", { name: "Prove your identity" })).toBeVisible();
  });

  await test.step("nominee submits identity for review", async () => {
    await nominee.getByLabel("Last 4 characters of your ID").fill(NOMINEE.last4);
    await nominee.getByLabel("Your date of birth").fill(NOMINEE.dob);
    await nominee.getByLabel("Photo or scan of your ID").setInputFiles(file("ravi-id.txt", "NATIONAL ID Ravi Rao"));
    await nominee.getByRole("button", { name: "Submit identity" }).click();
    await expect(nominee.getByRole("heading", { name: "Identity under review" })).toBeVisible();
  });

  await test.step("owner approves the nominee's identity", async () => {
    await nav(page, "Plan & nominees");
    await page.reload();
    await page.getByRole("button", { name: `Approve ID for ${NOMINEE.name}` }).click();
    await expectToast(page, `Identity approved for ${NOMINEE.name}.`);
    await expectAccessible(page, "plan section");
  });

  await test.step("nominee reports the death with a certificate", async () => {
    await nominee.reload();
    await expect(nominee.getByRole("heading", { name: "Report a death and request release" })).toBeVisible();
    await nominee.getByLabel("Date of death").fill("2026-11-01");
    await nominee.getByLabel("Death certificate").setInputFiles(file("death-certificate.txt", "CERTIFICATE OF DEATH Asha Rao"));
    await nominee.getByRole("button", { name: "Submit death claim" }).click();
    await expect(nominee.getByRole("heading", { name: "Release request" })).toBeVisible();
    await expectAccessible(nominee, "nominee release request");
  });

  await test.step("India requires proof of entitlement: nominee uploads a legal-heir certificate", async () => {
    await expect(nominee.getByRole("region", { name: "Legal documents for India" })).toBeVisible();
    await nominee.getByLabel("Document you have").selectOption("Legal heir certificate (Tahsildar / revenue authority)");
    await nominee.getByLabel(/^Upload it/).setInputFiles(file("legal-heir.txt", "LEGAL HEIR CERTIFICATE Ravi Rao"));
    await nominee.getByRole("button", { name: "Upload for review" }).click();
    await expectToast(nominee, "Legal document uploaded for review.");
  });

  await test.step("owner reviews the certificate; release still waits for the legal document", async () => {
    await page.reload();
    await page.getByRole("button", { name: "Approve certificate" }).click();
    await expectToast(page, /Certificate approved\. Request is now Open/);
  });

  await test.step("owner approves the legal-heir certificate; every gate passes", async () => {
    await page.getByRole("button", { name: /^Approve Legal heir certificate/ }).click();
    await expectToast(page, /Legal document approved\. Request is Released/);
    await expect(page.getByText("Hash chain intact", { exact: false })).toBeVisible();
  });

  await test.step("country rules page shows India's deadlines and privacy law", async () => {
    await nav(page, "Country & compliance");
    await expect(page.getByRole("heading", { level: 1, name: "Country & compliance" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Deadlines for India" })).toContainText("Register the death");
    await expect(page.getByText(/Digital Personal Data Protection Act 2023/).first()).toBeVisible();
    await expectAccessible(page, "country & compliance");
  });

  await test.step("nominee unlocks the released vault and downloads the will", async () => {
    await nominee.reload();
    await expect(nominee.getByRole("heading", { name: "Your released legacy vault" })).toBeVisible();
    await nominee.getByLabel("Authenticator code to unlock downloads").fill(await freshTotp(secret));
    await nominee.getByRole("button", { name: "Unlock downloads" }).click();
    await expect(nominee.getByText(/Downloads are unlocked until/)).toBeVisible();
    const downloading = nominee.waitForEvent("download");
    await nominee.getByRole("link", { name: "Download asha-will.txt" }).click();
    const download = await downloading;
    const body = await (await download.createReadStream()).toArray();
    expect(Buffer.concat(body).toString()).toBe(WILL);
    await expectAccessible(nominee, "released vault");
  });
  await nominee.context().close();
});

test("Discovery: from a phone and one statement to confirmed assets", async ({ page }) => {
  await test.step("owner signs in and starts a Discovery case", async () => {
    await ownerSignIn(page);
    await newCase(page, "Discovery", "Vikram Mehta");
    await expect(page.getByRole("heading", { level: 1, name: "Discovery · Start from anything" })).toBeVisible();
  });

  await test.step("tell Trove what the family has", async () => {
    await page.getByLabel("Their phone (unlocked)").check();
    await page.getByLabel("One or two documents").check();
    await page.getByLabel("Their phone numbers").fill("+91 98123 45678");
    await page.getByRole("button", { name: "Build my starting plan" }).click();
    await expectToast(page, "Starting plan updated.");
    await expect(page.locator(".plan-list li").first()).toBeVisible();
    await expectAccessible(page, "discovery step 1");
  });

  await test.step("scan an SMS export and a bank statement", async () => {
    await page.getByRole("tab", { name: /Add sources/ }).click();
    await page.getByLabel("What is it?").selectOption("sms_export");
    await page.getByLabel(/^File/).setInputFiles(
      file(
        "sms.xml",
        '<?xml version="1.0"?><smses><sms address="VM-HDFCBK" date="1714600000000" body="Rs 5000 debited from A/c XX4321 towards LIC premium"/>' +
          '<sms address="AD-ZERODH" date="1714700000000" body="Contract note for your trades is emailed"/></smses>',
      ),
    );
    await page.getByRole("button", { name: "Scan source" }).click();
    await expectToast(page, /Scanned 2 items/);
    await page.getByLabel("What is it?").selectOption("document");
    await page.getByLabel("Document type").selectOption("bank_statement");
    await page.getByLabel(/^File/).setInputFiles(
      file("statement.txt", "HDFC Bank statement for Vikram Mehta\nA/c XXXX4321\n02/05 NACH DR LIC PREMIUM 5000\n05/05 SIP ICICI Prudential MF 2000\n"),
    );
    await page.getByRole("button", { name: "Scan source" }).click();
    await expectToast(page, /Scanned 1 item/);
  });

  await test.step("analyse the fictional demo inbox", async () => {
    await page.getByLabel("Demo email address").fill("vikram@example.test");
    await page.getByRole("button", { name: "Load sample inbox" }).click();
    await expectToast(page, /Loaded \d+ fictional emails/);
    await page.getByRole("button", { name: "Select all" }).click();
    await page.getByRole("button", { name: /^Analyse \d+ messages/ }).click();
    await expectToast(page, /Analysed \d+ emails/);
    await expectAccessible(page, "discovery sources");
  });

  await test.step("leads seen in two sources are corroborated; promote one", async () => {
    await page.getByRole("tab", { name: /Leads/ }).click();
    const hdfc = page.getByRole("row", { name: /HDFC Bank/ }).first();
    await expect(hdfc).toContainText("2");
    await page.getByRole("button", { name: /^Promote .*HDFC Bank/ }).first().click();
    await expectToast(page, "Lead added to the asset register for review.");
    await expectAccessible(page, "leads table");
  });

  await test.step("confirm the promoted asset in the overview", async () => {
    await nav(page, "Overview");
    await page.getByRole("button", { name: /^Confirm .*HDFC Bank/ }).first().click();
    await expectToast(page, /HDFC Bank: confirmed/);
  });

  await test.step("knowledge graph links the evidence", async () => {
    await nav(page, "Knowledge graph");
    await expect(page.getByRole("img", { name: /Network of/ })).toBeVisible();
    await expect(page.getByRole("table", { name: "Graph nodes and their connections" })).toContainText("HDFC Bank");
    await expectAccessible(page, "knowledge graph");
  });

  await test.step("assistant answers locally", async () => {
    await nav(page, "Assistant");
    await page.getByLabel("Your question").fill("What should I do next?");
    await page.getByRole("button", { name: "Ask Trove" }).click();
    await expect(page.getByRole("log", { name: "Conversation" })).toContainText("Answered locally");
    await expectAccessible(page, "assistant");
  });
});
