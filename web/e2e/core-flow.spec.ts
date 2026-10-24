import { expect, test } from "@playwright/test";

/**
 * The core flow (M6-T09, TRD §14 M6 exit criterion): pick a language, accept the privacy
 * notice, ask about eligibility, see a reply, and check "what Sahayak knows about me".
 *
 * Needs a real `sahayak` API reachable at VITE_API_URL (default http://localhost:8000) with an
 * LLM provider configured (`just up`, or `uv run just dev` with a filled-in `.env`) and at least
 * one scheme ingested (M1) — this is why it's run on demand rather than in CI, which has neither
 * (see the project README and docs/DECISIONS.md D-046).
 */
test("a non-technical user can chat and see what Sahayak knows", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("radio", { name: "English" }).click();
  await page.getByRole("button", { name: /i agree, let's start/i }).click();

  await expect(page.getByRole("button", { name: /speak/i })).toBeVisible();

  const input = page.getByLabel(/type your message/i);
  await input.fill("I am a farmer, am I eligible for any scheme?");
  const sendButton = page.getByRole("button", { name: /^send$/i });
  await sendButton.click();

  // Two bubbles (the user's message and Sahayak's reply) once the turn completes.
  await expect(page.getByRole("group", { name: "Sahayak" })).toBeVisible({ timeout: 20_000 });

  await page.getByRole("button", { name: /what sahayak knows about me/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: /close/i }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
