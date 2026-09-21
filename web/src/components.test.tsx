import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import {
  AnswerCard,
  Composer,
  Evidence,
  Dialog,
  Status,
  MisconceptionCheck,
} from "./components";
const citation = {
  source_id: "s",
  source_name: "Reference",
  url: "https://example.org",
  text: "A real extracted passage.",
  char_start: 0,
  char_end: 25,
  tier: 2,
  score: 1,
};
describe("honest evidence UI", () => {
  it("never styles a failed answer as grounded even with stale status", () => {
    render(
      <AnswerCard
        answer={{
          draft: "Unsupported statement",
          grounded: false,
          status: "grounded",
          citations: [],
          failures: ["missing-citation"],
        }}
      />,
    );
    expect(screen.queryByText("GROUNDED")).not.toBeInTheDocument();
    expect(screen.getByText("UNVERIFIED")).toBeInTheDocument();
    expect(screen.getByText("missing-citation")).toBeInTheDocument();
  });
  it("renders source text as data, never HTML", () => {
    render(
      <Evidence
        citations={[{ ...citation, text: "<img src=x onerror=alert(1)>" }]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Reference/ }));
    expect(
      screen.getByText("<img src=x onerror=alert(1)>"),
    ).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
  it("blocks unsafe citation links", () => {
    render(
      <Evidence citations={[{ ...citation, url: "javascript:alert(1)" }]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Reference/ }));
    expect(screen.queryByRole("link")).toBeNull();
  });
  it("renders supported answer without interpreting HTML", () => {
    render(
      <AnswerCard
        answer={{
          draft: "## Claim\nA qubit <script>bad</script> [1]",
          grounded: true,
          status: "grounded",
          citations: [citation],
        }}
      />,
    );
    expect(screen.getByText("GROUNDED")).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });
  it("inline evidence markers open their source and bibliography is not duplicated", () => {
    render(
      <AnswerCard
        answer={{
          draft:
            "## Claim\nSupported sentence [1]\n## Citations\n[1] Reference — https://example.org",
          grounded: true,
          status: "grounded",
          citations: [citation],
        }}
      />,
    );
    expect(screen.queryByRole("heading", { name: "Citations" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open evidence 1" }));
    expect(screen.getByText(citation.text)).toBeInTheDocument();
  });
  it("a failure status wins over a contradictory positive flag", () => {
    render(<Status status="oracle-failed" grounded={true} />);
    expect(screen.getByText("ORACLE FAILED")).toHaveClass("bad");
  });
  it("shows oracle failure as failure", () => {
    render(<Status status="oracle-failed" />);
    expect(screen.getByText("ORACLE FAILED")).toHaveClass("bad");
  });
});
it("misconception callouts show only validated node references, never invented beliefs", () => {
  render(
    <MisconceptionCheck
      triggered={["known", "forged"]}
      misconceptions={[
        { id: "known", text: "A qubit is merely a hidden classical bit." },
      ]}
    />,
  );
  expect(screen.getByText("Misconception check")).toBeInTheDocument();
  expect(screen.getByText(/A qubit is merely/)).toBeInTheDocument();
  expect(screen.queryByText("forged")).toBeNull();
});
describe("accessible interactions", () => {
  it("keeps an unsent question when the request fails", async () => {
    const send = vi.fn(async () => false);
    render(<Composer onSend={send} busy={false} />);
    const input = screen.getByRole("textbox", { name: "Your question" });
    fireEvent.change(input, { target: { value: "Keep my question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send question" }));
    await vi.waitFor(() => expect(send).toHaveBeenCalled());
    expect(input).toHaveValue("Keep my question");
  });
  it("submits a question, not empty or duplicate while busy", () => {
    const send = vi.fn();
    const { rerender } = render(<Composer onSend={send} busy={false} />);
    const input = screen.getByRole("textbox", { name: "Your question" });
    fireEvent.change(input, { target: { value: "Explain qubits" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(send).toHaveBeenCalledWith("Explain qubits");
    rerender(<Composer onSend={send} busy={true} />);
    expect(
      screen.getByRole("button", { name: "Send question" }),
    ).toBeDisabled();
  });
  it("allows multiline questions without submitting", () => {
    const send = vi.fn();
    render(<Composer onSend={send} busy={false} />);
    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      shiftKey: true,
    });
    expect(send).not.toHaveBeenCalled();
  });
  it("keeps dialogs outside clipped learning surfaces", () => {
    const { unmount } = render(
      <div style={{ overflow: "hidden", transform: "translateZ(0)" }}>
        <Dialog title="Evidence details" onClose={vi.fn()}>
          <button>Inside</button>
        </Dialog>
      </div>,
    );
    const backdrop = screen.getByRole("dialog").parentElement;
    expect(backdrop?.parentElement).toBe(document.body);
    unmount();
    expect(document.querySelector(".modal-backdrop")).toBeNull();
  });
  it("Escape only closes the topmost dialog", () => {
    const parent = vi.fn(),
      child = vi.fn();
    const originalOverflow = document.body.style.overflow;
    const { unmount } = render(
      <Dialog title="Parent" onClose={parent}>
        <Dialog title="Child" onClose={child}>
          <button>Child action</button>
        </Dialog>
      </Dialog>,
    );
    const childDialog = screen.getByRole("dialog", { name: "Child" });
    expect(childDialog).toContainElement(document.activeElement as HTMLElement);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(child).toHaveBeenCalledOnce();
    expect(parent).not.toHaveBeenCalled();
    unmount();
    expect(document.body.style.overflow).toBe(originalOverflow);
  });
  it("closes dialogs with Escape", () => {
    const close = vi.fn();
    render(
      <Dialog title="Evidence details" onClose={close}>
        <button>Inside</button>
      </Dialog>,
    );
    expect(
      screen.getByRole("dialog", { name: "Evidence details" }),
    ).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(close).toHaveBeenCalled();
  });
});
