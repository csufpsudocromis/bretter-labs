import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "axios";

import AdminImages from "../src/components/admin/AdminImages.jsx";
import { api } from "../src/api";

vi.mock("axios", () => ({
  default: {
    post: vi.fn(),
  },
}));

vi.mock("../src/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
  },
}));

describe("AdminImages upload flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uploads via direct path and waits for finalize completion", async () => {
    const user = userEvent.setup();
    const state = {
      images: [],
      pollCount: 0,
    };

    api.get.mockImplementation(async (url) => {
      if (url === "/admin/images") {
        return { data: state.images };
      }
      if (url === "/auth/me") {
        return { data: { role: "platform_admin" } };
      }
      if (url === "/admin/images/upload-tasks/task-1234") {
        state.pollCount += 1;
        state.images = [
          {
            id: "img-1",
            name: "win11.vdi",
            size_bytes: 1024 * 1024 * 8,
          },
        ];
        return {
          data: {
            task_id: "task-1234",
            status: "completed",
            stage: "completed",
            progress_percent: 100,
            detail: "Image ready",
            retry_count: 0,
            max_retries: 3,
          },
        };
      }
      throw new Error(`unexpected GET ${url}`);
    });

    api.post.mockImplementation(async (url) => {
      if (url === "/admin/images/direct-upload/start") {
        return {
          data: {
            upload_url: "https://upload.example/v1beta1/upload",
            upload_token: "token-123",
            task: {
              task_id: "task-1234",
            },
          },
        };
      }
      throw new Error(`unexpected POST ${url}`);
    });

    axios.post.mockResolvedValue({ status: 200 });

    render(<AdminImages />);

    await screen.findByText("Upload image");
    const fileInput = document.querySelector('input[type="file"]');
    if (!fileInput) {
      throw new Error("file input not found");
    }
    const file = new File(["dummy"], "win11.vdi", { type: "application/octet-stream" });
    await user.upload(fileInput, file);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => {
      expect(screen.getByText("win11.vdi")).toBeInTheDocument();
    });
    expect(api.post).toHaveBeenCalledWith("/admin/images/direct-upload/start", {
      filename: "win11.vdi",
      size_bytes: 5,
    });
    expect(api.get).toHaveBeenCalledWith("/admin/images/upload-tasks/task-1234");
    expect(axios.post).toHaveBeenCalled();
  });
});
