import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UserPanel from "../src/components/UserPanel.jsx";
import { api } from "../src/api";

vi.mock("../src/api", () => ({
  api: {
    defaults: {
      baseURL: "https://labs.example/api",
    },
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

describe("UserPanel VM flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("supports start, connect, and delete for VM labs", async () => {
    const user = userEvent.setup();
    const openSpy = vi.spyOn(window, "open").mockReturnValue({ closed: false, postMessage: vi.fn() });

    const state = {
      templates: [{ id: "tmpl-1", name: "Windows Lab", description: "RDP template", idle_timeout_minutes: 30 }],
      instances: [],
      containerTemplates: [],
      containerInstances: [],
    };

    api.get.mockImplementation(async (url) => {
      if (url === "/user/templates") return { data: state.templates };
      if (url === "/user/pods") return { data: state.instances };
      if (url === "/user/container-templates") return { data: state.containerTemplates };
      if (url === "/user/containers") return { data: state.containerInstances };
      throw new Error(`unexpected GET ${url}`);
    });

    api.post.mockImplementation(async (url) => {
      if (url === "/user/templates/tmpl-1/start") {
        state.instances = [
          {
            id: "inst-1",
            template_id: "tmpl-1",
            owner: "alice",
            status: "running",
            status_stage: "running",
            status_detail: "VM is running.",
            console_url: "https://labs.example/user/pods/inst-1/connect/rdp.html",
          },
        ];
        return { data: { id: "inst-1" } };
      }
      if (url === "/user/pods/inst-1/connect-token") {
        return { data: { connect_url: "https://labs.example/user/pods/inst-1/connect/rdp.html" } };
      }
      throw new Error(`unexpected POST ${url}`);
    });

    api.delete.mockImplementation(async (url) => {
      if (url === "/user/pods/inst-1") {
        state.instances = [];
        return { status: 204 };
      }
      throw new Error(`unexpected DELETE ${url}`);
    });

    render(<UserPanel />);

    await screen.findByText("Windows Lab");
    await user.click(screen.getByRole("button", { name: "Start Lab" }));

    const connectButton = await screen.findByRole("button", { name: "Connect" });
    expect(connectButton).toBeEnabled();
    await user.click(connectButton);

    expect(api.post).toHaveBeenCalledWith("/user/pods/inst-1/connect-token");
    expect(openSpy).toHaveBeenCalledWith("https://labs.example/user/pods/inst-1/connect/rdp.html", "_blank");

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(screen.getByText("No labs yet. Start a lab to see it here.")).toBeInTheDocument();
    });
    expect(api.delete).toHaveBeenCalledWith("/user/pods/inst-1");
    openSpy.mockRestore();
  });
});
