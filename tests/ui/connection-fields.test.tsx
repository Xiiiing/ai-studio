import { Field } from "../../ui/factory/src/ui";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import {
  CredentialField,
  endpointValues,
  protocolName,
} from "../../ui/factory/src/ConnectionFields";

it("normalizes call URLs without removing gateway prefixes and derives network scope", () => {
  expect(
    endpointValues({
      provider: "openai_compatible",
      base_url: "https://gateway.example/inference/writer/v1/chat/completions",
    }),
  ).toEqual({
    base_url: "https://gateway.example/inference/writer/v1",
    network_mode: "remote",
    allowed_hosts: ["gateway.example"],
  });
  expect(
    endpointValues({
      provider: "llama_cpp",
      base_url: "http://127.0.0.1:8090",
    }),
  ).toEqual({
    base_url: "http://127.0.0.1:8090/v1",
    network_mode: "loopback",
    allowed_hosts: [],
  });
  expect(
    endpointValues({
      provider: "ollama_native",
      base_url: "http://172.20.114.10:11434/api/chat",
    }),
  ).toEqual({
    base_url: "http://172.20.114.10:11434",
    network_mode: "lan",
    allowed_hosts: ["172.20.114.10"],
  });
  expect(
    endpointValues({
      provider: "gemini",
      base_url:
        "https://proxy.example/v1beta/models/gemini-test:generateContent",
    }).base_url,
  ).toBe("https://proxy.example/v1beta");
  expect(
    endpointValues({
      provider: "openai_compatible",
      base_url: "https://[2001:4860:4860::8888]/v1",
    }).network_mode,
  ).toBe("remote");
  for (const base_url of [
    "https://user:pass@example.com",
    "https://example.com?key=private",
    "https://example.com/#private",
  ])
    expect(() => endpointValues({ base_url })).toThrow(/URL/);
});
it("labels actual native protocols separately from OpenAI Chat Completions", () => {
  expect(protocolName("anthropic")).toContain("Messages");
  expect(protocolName("gemini")).toContain("generateContent");
  expect(protocolName("openai_compatible")).toContain("Chat Completions");
});
it("masks a write-only key by default and never reads it from browser storage", async () => {
  render(<CredentialField saved />);
  const input = document.querySelector(
    'input[name="api_key"]',
  ) as HTMLInputElement;
  expect(input.value).toBe("");
  expect(input.type).toBe("password");
  await userEvent.type(input, "fixture-only-token");
  await userEvent.click(screen.getByRole("button", { name: "显示 API Key" }));
  expect(input.type).toBe("text");
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});

it("does not give independently labelled choices the same field ID", async () => {
  const { container } = render(
    <Field label="选择镜头">
      <div>
        <label>
          <input type="checkbox" />
          镜头 1
        </label>
        <label>
          <input type="checkbox" />
          镜头 2
        </label>
      </div>
    </Field>,
  );
  const ids = Array.from(container.querySelectorAll("[id]")).map(
    (element) => element.id,
  );
  expect(new Set(ids).size).toBe(ids.length);
  await userEvent.click(screen.getByRole("checkbox", { name: "镜头 2" }));
  expect(screen.getByRole("checkbox", { name: "镜头 2" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "镜头 1" })).not.toBeChecked();
});
