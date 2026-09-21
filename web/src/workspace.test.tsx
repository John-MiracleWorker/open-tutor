import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LessonEvent, lessonEvent, PathArtwork } from "./workspace";

describe("quiet lesson requests", () => {
  it("keeps real learner text intact even when it resembles a host request", () => {
    expect(lessonEvent("Start a guided lesson on pizza but I have my own question")).toBeNull();
    render(<LessonEvent content="Here is my own answer" />);
    expect(screen.getByText("Here is my own answer")).toHaveClass("user-bubble");
  });
  it("compacts exact host asks without deleting their original content", () => {
    const text = "Keep teaching me: take the next small step from my last answer.";
    render(<LessonEvent content={text} />);
    const summary = screen.getByText("Continuing to the next small step");
    expect(summary.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(summary);
    expect(screen.getByText(text)).toBeInTheDocument();
  });
  it("keeps vector cover gradients unique across courses", () => {
    const { container } = render(<><PathArtwork /><PathArtwork variant={1}/><PathArtwork variant={2}/></>);
    const ids = Array.from(container.querySelectorAll("linearGradient")).map(x => x.id);
    expect(new Set(ids).size).toBe(3);
    expect(container.querySelectorAll('svg[aria-hidden="true"]')).toHaveLength(3);
  });
});
