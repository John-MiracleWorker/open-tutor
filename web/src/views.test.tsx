import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { Designer, Research, Progress, Library, Assessment, SettingsPanel } from "./views";
import { api } from "./api";
vi.mock("./api", () => ({
  api: vi.fn(async () => ({ job_id: "j", sources: [], errors: [] })),
  enc: encodeURIComponent,
  watchJob: vi.fn(async () => ({
    id: "j",
    status: "completed",
    stage: "done",
    result: { subject: "test", decision: { status: "compiled" } },
  })),
}));
it("settings distinguishes adaptive local-model teaching from legacy evidence mode", async () => {
 vi.mocked(api).mockResolvedValue({base_url:"http://localhost:9120",model:"local",mode:"extractive"});
 render(<SettingsPanel onClose={()=>{}}/>);
 expect(await screen.findByText(/Adaptive teaching always uses your local model/)).toBeInTheDocument();
 expect(screen.getByLabelText("Legacy evidence-answer mode")).toBeInTheDocument();
});
it("settings save omits the derived configured flag the PUT contract rejects", async () => {
 vi.mocked(api)
  .mockResolvedValueOnce({base_url:"http://localhost:9120",model:"local",mode:"extractive",configured:true} as never)
  .mockResolvedValueOnce({base_url:"http://localhost:9120",model:"local",mode:"local-model",configured:true} as never);
 render(<SettingsPanel onClose={()=>{}}/>);
 const select = await screen.findByLabelText("Legacy evidence-answer mode");
 fireEvent.change(select, { target: { value: "local-model" } });
 fireEvent.click(screen.getByRole("button", { name: /Save settings/ }));
 const putCall = await vi.waitFor(() =>
   vi.mocked(api).mock.calls.find((c) => c[0] === "/settings" && c[2] === "PUT"),
 );
 const [, body, method] = putCall as [string, Record<string, unknown>, string];
 expect(method).toBe("PUT");
 expect(body).toEqual({ base_url: "http://localhost:9120", model: "local", mode: "local-model" });
 expect(body).not.toHaveProperty("configured");
});
it("designer offers optional review and preserves user scope", () => {
  render(
    <Designer
      initialTopic="Calculus"
      onClose={() => {}}
      onComplete={() => {}}
    />,
  );
  expect(screen.getByLabelText("What would you like to learn?")).toHaveValue(
    "Calculus",
  );
  expect(
    screen.getByLabelText("Review before adding to my library"),
  ).not.toBeChecked();
  expect(screen.getByLabelText("Starting point")).toBeInTheDocument();
  expect(screen.getByLabelText("Depth")).toBeInTheDocument();
});
it("candidate-only curricula cannot start learning and are reviewable from subjects", async () => {
  const s = {
    subject: "test",
    title: "Test curriculum",
    node_count: 1,
    grounded_count: 0,
    source_count: 1,
    status: "blocked",
    candidate: { status: "blocked", fingerprint: "abc" },
  };
  render(
    <Library
      subjects={[s]}
      onChoose={() => {}}
      onDesign={() => {}}
      selected={null}
      onRefresh={async () => {}}
    />,
  );
  expect(
    screen.queryByRole("button", { name: /Explore curriculum/ }),
  ).toBeNull();
  expect(screen.getByText("Curricula awaiting attention")).toBeInTheDocument();
  expect(
    vi.mocked(api).mock.calls.some(([path]) => path === "/candidates"),
  ).toBe(false);
});
it("practice renders server options and returns exact issued evidence, not a self-score", async () => {
  const evidence = [
    {
      source_id: "s",
      text: "Exact source passage.",
      char_start: 5,
      char_end: 26,
    },
  ];
  vi.mocked(api).mockImplementation(async (path, body) =>
    body
      ? { grade: { verdict: "correct", score: 1 } }
      : {
          item: {
            id: "server-item",
            kind: "quiz",
            prompt: "Pick one",
            answer_options: ["A. First", "B. Second"],
          },
          evidence,
        },
  );
  render(
    <Assessment
      kind="quiz"
      subject="science"
      node={{
        id: "n",
        title: "Concept",
        defn: "Definition",
        prereqs: [],
        status: "grounded",
        grounding_corpus: ["s"],
        misconceptions: [],
      }}
      onClose={() => {}}
      onGraded={() => {}}
    />,
  );
  fireEvent.click(await screen.findByRole("radio", { name: "B. Second" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Check my understanding" }),
  );
  expect(
    await screen.findByText("That idea is taking root."),
  ).toBeInTheDocument();
  expect(api).toHaveBeenCalledWith("/subjects/science/assessment", {
    item_id: "server-item",
    response: { text: "B", citations: evidence },
  });
});
it("progress distinguishes practice items from legacy interaction counts", () => {
  const detail = {
    spec: {
      subject: "s",
      title: "S",
      nodes: [],
      corpus: [],
      scope: {},
      tiers: {},
    },
    report: {},
    state: {
      nodes: { n: { mastery: 0.5, attempts: 99, practice_attempts: 3 } },
    },
    gates: {},
    due: [],
  };
  render(
    <Progress detail={detail} onPractice={() => {}} onExplore={() => {}} />,
  );
  expect(
    screen.getByText("Practice attempts").closest(".stat-card"),
  ).toHaveTextContent("03");
});
it("a verified non-material candidate still has an apply action",async()=>{
 const spec={subject:"s",title:"Candidate title",nodes:[],corpus:[],scope:{},tiers:{}};
 vi.mocked(api).mockResolvedValue({spec,report:{},decision:{status:"compiled"},state:{nodes:{}},gates:{},due:[],candidate:{spec,report:{},decision:{status:"compiled"},fingerprint:"abc"}});
 render(<Library subjects={[{subject:"s",title:"Test",node_count:0,grounded_count:0,source_count:0,status:"compiled",candidate:{status:"compiled",fingerprint:"abc"}}]} onChoose={()=>{}} onDesign={()=>{}} selected={null} onRefresh={async()=>{}}/>);
 fireEvent.click(screen.getByRole("button",{name:"Review Test"}));
 expect(await screen.findByRole("button",{name:"Add verified curriculum"})).toBeEnabled();
});
it("progress never invents progress for an empty library", () => {
  render(<Progress detail={null} onPractice={() => {}} onExplore={() => {}} />);
  expect(
    screen.getByText("Understanding starts with a question."),
  ).toBeInTheDocument();
  expect(screen.queryByText("80%")).toBeNull();
});
it("research labels discovery as unverified", async () => {
  render(<Research detail={null} onDesign={() => {}} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Research a topic" }), {
    target: { value: "calculus" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Find sources" }));
  expect(await screen.findByText(/No matching sources/)).toBeInTheDocument();
});
