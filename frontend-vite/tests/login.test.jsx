import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Login from "../src/components/Login.jsx";
import { api } from "../src/api";

vi.mock("../src/api", () => ({
  api: {
    get: vi.fn(),
  },
}));

describe("Login", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.get.mockResolvedValue({
      data: {
        sso_enabled: false,
        sso_authorize_url: "",
      },
    });
  });

  it("submits credentials through the login callback", async () => {
    const user = userEvent.setup();
    const onLogin = vi.fn();

    render(<Login onLogin={onLogin} user={null} />);

    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "password");
    await user.click(screen.getByRole("button", { name: "Sign In" }));

    expect(onLogin).toHaveBeenCalledTimes(1);
    expect(onLogin).toHaveBeenCalledWith("alice", "password");
  });
});
