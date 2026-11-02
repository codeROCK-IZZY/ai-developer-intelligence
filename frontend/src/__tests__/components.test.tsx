import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CodeView, FindingCounts, GradeBadge, SymbolLink, fileHref } from "../components/code";
import { Badge, SourceTag } from "../components/ui";
import { humanize } from "../lib/format";
import { DependencyDiagram } from "../pages/RepositoryPage";

describe("labels and formatting", () => {
  it("labels AI output distinctly from rule-based output", () => {
    const { rerender } = render(<SourceTag source="claude:claude-opus-5-5" />);
    expect(screen.getByText("AI · claude-opus-5-5")).toBeInTheDocument();
    rerender(<SourceTag source="rule:offline" />);
    expect(screen.getByText("Rule-based")).toBeInTheDocument();
  });
  it("renders badges and grades", () => {
    render(<><Badge tone="green">ready</Badge><GradeBadge grade="B" health={81} /></>);
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByTitle("Health 81/100 (rule-based)")).toHaveTextContent("B");
    expect(humanize("import_cycle")).toBe("import cycle");
  });
  it("summarises finding counts by severity, highest first", () => {
    render(<FindingCounts counts={{ low: 3, high: 1 }} />);
    const badges = screen.getAllByText(/high|low/);
    expect(badges.map((b) => b.textContent)).toEqual(["1 high", "3 low"]);
  });
});

describe("code components", () => {
  it("numbers lines from the start line and marks findings", () => {
    render(<CodeView code={"def f():\n    return 1"} startLine={10} findings={[{
      id: 1, rule: "todo", title: "TODO", category: "maintainability", severity: "info", path: "a.py", file_id: 1,
      line: 11, symbol_id: null, symbol: null, message: "fix me", evidence: "", fix: "",
    }]} />);
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByTitle("TODO: fix me")).toBeInTheDocument();
  });
  it("links symbols and files", () => {
    render(<MemoryRouter><SymbolLink id={7} name="Cart.total" /><SymbolLink id={null} name="external" /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "Cart.total" })).toHaveAttribute("href", "/symbols/7");
    expect(screen.queryByRole("link", { name: "external" })).toBeNull();
    expect(fileHref("r1", "app/x.py", 12)).toBe("/repositories/r1?tab=files&path=app%2Fx.py&line=12");
  });
  it("draws the dependency diagram with mutual imports highlighted", () => {
    const { container } = render(<DependencyDiagram graph={{ level: "directory", cycles: [],
      nodes: [{ id: "a", files: 1, loc: 10, tests: 0, health: 95 }, { id: "b", files: 2, loc: 40, tests: 1, health: 50 }],
      edges: [{ source: "a", target: "b", imports: 1, mutual: true }, { source: "b", target: "a", imports: 2, mutual: true }] }} />);
    expect(container.querySelectorAll("circle")).toHaveLength(2);
    expect(container.querySelectorAll('line[stroke="#d97706"]')).toHaveLength(2);
  });
});
