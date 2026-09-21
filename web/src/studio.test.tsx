import { render, screen, fireEvent, within } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { Library } from "./views";
import { CourseOutline } from "./workspace";
import type { Detail, Node } from "./types";

const nodes: Node[] = [
  { id: "q", title: "The Qubit", defn: "A foundation", prereqs: [], status: "grounded", grounding_corpus: [], misconceptions: [] },
  { id: "s", title: "Superposition", defn: "Another idea", prereqs: ["q"], status: "grounded", grounding_corpus: [], misconceptions: [] },
];
const detail: Detail = { spec: { subject: "quantum", title: "Quantum Computing", nodes, corpus: [], scope: {}, tiers: {} }, report: {}, state: { nodes: {} }, gates: { s: { unlocked: false, blocking_prereqs: ["q"] } }, due: [] };
const subjects = [
  { subject: "quantum", title: "Quantum Computing", status: "compiled", node_count: 2, source_count: 1, grounded_count: 2 },
  { subject: "music", title: "Music Theory", status: "compiled", node_count: 3, source_count: 2, grounded_count: 3 },
  { subject: "blocked", title: "Unverified Topic", status: "blocked", node_count: 1, source_count: 0, grounded_count: 0, candidate: { status: "blocked", fingerprint: "f" } },
];
it("filters the learning path without changing concept identity or prerequisite labels", () => {
  const select = vi.fn();
  render(<CourseOutline detail={detail} selected="q" onSelect={select} />);
  fireEvent.change(screen.getByRole("searchbox", { name: "Find a concept" }), { target: { value: "SUPER" } });
  expect(screen.queryByRole("button", { name: /The Qubit/ })).toBeNull();
  const concept = screen.getByRole("button", { name: /Superposition/ });
  expect(concept).toHaveTextContent("Prerequisite practice needed");
  fireEvent.click(concept);
  expect(select).toHaveBeenCalledWith("s");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "not here" } });
  expect(screen.getByText("No matching concepts.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Clear concept search" }));
  expect(screen.getByRole("button", { name: /The Qubit/ })).toHaveAttribute("aria-pressed", "true");
});
it("resumes the actual selected course without inventing progress", () => {
  const choose = vi.fn();
  render(<Library subjects={subjects} selected={detail} onChoose={choose} onDesign={() => {}} onRefresh={async () => {}} />);
  const resume = screen.getByRole("region", { name: "Pick up where you left off" });
  expect(within(resume).getByRole("heading", { name: "Quantum Computing" })).toBeInTheDocument();
  expect(resume).toHaveTextContent("2 concepts");
  expect(resume).not.toHaveTextContent("% complete");
  fireEvent.click(within(resume).getByRole("button", { name: "Continue learning" }));
  expect(choose).toHaveBeenCalledWith("quantum");
});
it("keeps long course subtitles readable without discarding the title", () => {
  const title = "Quantum Computing — foundations to a first algorithm";
  render(<Library subjects={[{ ...subjects[0], title }]} selected={detail} onChoose={() => {}} onDesign={() => {}} onRefresh={async () => {}} />);
  const resume = screen.getByRole("region", { name: "Pick up where you left off" });
  expect(within(resume).getByRole("heading", { name: title })).toBeInTheDocument();
  expect(resume.querySelector(".resume-subtitle")).toHaveTextContent("— foundations to a first algorithm");
});
it("searches saved courses and keeps blocked candidates discoverable", () => {
  render(<Library subjects={subjects} selected={detail} onChoose={() => {}} onDesign={() => {}} onRefresh={async () => {}} />);
  fireEvent.change(screen.getByRole("searchbox", { name: "Find a curriculum" }), { target: { value: "music" } });
  const collection = screen.getByRole("region", { name: "Your curricula" });
  expect(within(collection).getByRole("heading", { name: "Music Theory" })).toBeInTheDocument();
  expect(within(collection).queryByRole("heading", { name: "Quantum Computing" })).toBeNull();
  expect(screen.getByText("Curricula awaiting attention")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox", { name: "Find a curriculum" }), { target: { value: "unknown" } });
  expect(screen.getByText("No curricula match your search.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Clear curriculum search" }));
  expect(within(collection).getAllByRole("article")).toHaveLength(2);
});
it("searches curriculum and concept titles deterministically under a Turkish default locale", () => {
  const localeLowerCase = vi.spyOn(String.prototype, "toLocaleLowerCase").mockImplementation(function (this: string) {
    return String(this).replaceAll("I", "ı").toLowerCase();
  });
  const introductionNode: Node = {
    id: "introduction",
    title: "Introduction",
    defn: "A starting point",
    prereqs: [],
    status: "grounded",
    grounding_corpus: [],
    misconceptions: [],
  };
  const introductionDetail: Detail = {
    ...detail,
    spec: { ...detail.spec, nodes: [introductionNode] },
  };

  try {
    const { unmount } = render(
      <Library
        subjects={[{ ...subjects[0], title: "Introduction" }]}
        selected={introductionDetail}
        onChoose={() => {}}
        onDesign={() => {}}
        onRefresh={async () => {}}
      />,
    );
    fireEvent.change(screen.getByRole("searchbox", { name: "Find a curriculum" }), { target: { value: "introduction" } });
    const curriculumVisible = within(screen.getByRole("region", { name: "Your curricula" }))
      .queryByRole("heading", { name: "Introduction" }) !== null;
    unmount();

    render(<CourseOutline detail={introductionDetail} selected="introduction" onSelect={() => {}} />);
    fireEvent.change(screen.getByRole("searchbox", { name: "Find a concept" }), { target: { value: "introduction" } });
    const conceptVisible = screen.queryByRole("button", { name: /Introduction/ }) !== null;

    expect({ curriculumVisible, conceptVisible }).toEqual({ curriculumVisible: true, conceptVisible: true });
  } finally {
    localeLowerCase.mockRestore();
  }
});
