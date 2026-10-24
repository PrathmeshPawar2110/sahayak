import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

beforeEach(() => {
  window.HTMLElement.prototype.scrollTo = vi.fn();
});

afterEach(() => {
  localStorage.clear();
});

describe("the core flow", () => {
  it("shows the consent screen, then the chat after accepting", async () => {
    const user = userEvent.setup();
    render(<App />);

    const englishButton = await screen.findByRole("radio", { name: "English" });
    await user.click(englishButton);

    const acceptButton = screen.getByRole("button", { name: /i agree/i });
    await user.click(acceptButton);

    expect(await screen.findByRole("button", { name: /speak/i })).toBeInTheDocument();
  });

  it("sends a text message and shows the reply with an eligibility card", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("radio", { name: "English" }));
    await user.click(screen.getByRole("button", { name: /i agree/i }));

    const input = await screen.findByLabelText(/type your message/i);
    await user.type(input, "Am I eligible for anything?");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("echo: Am I eligible for anything?")).toBeInTheDocument();
    expect(await screen.findByText("PM-KISAN")).toBeInTheDocument();
    expect(await screen.findByText(/you can apply/i)).toBeInTheDocument();
  });

  it("opens the profile panel and shows what Sahayak knows", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("radio", { name: "English" }));
    await user.click(screen.getByRole("button", { name: /i agree/i }));

    await user.click(await screen.findByRole("button", { name: /what sahayak knows about me/i }));

    await waitFor(() => expect(screen.getByText("farmer")).toBeInTheDocument());
    expect(screen.getByText("45")).toBeInTheDocument();
  });
});
