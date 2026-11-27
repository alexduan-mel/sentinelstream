// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { activeNavKey } from "../components/TopBar";
import { installDomStubs } from "../test/dom";
import { GlossaryPage } from "./GlossaryPage";

beforeAll(installDomStubs);
afterEach(cleanup);

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/glossary"]}>
      <GlossaryPage />
    </MemoryRouter>
  );
}

describe("GlossaryPage", () => {
  it("shows every section with its terms, bold and code rendered", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: /^Glossary · \d+$/ })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "SEC and filings" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Research" })).toBeTruthy();
    expect(document.getElementById("cik")?.textContent).toContain("Central Index Key");
    expect(screen.getAllByText("0001045810")[0].tagName).toBe("CODE");
    expect(screen.getAllByText("1.01")[0].tagName).toBe("STRONG");
  });

  it("narrows to matching terms and says when nothing matches", () => {
    renderPage();
    const search = screen.getByLabelText("Search the glossary");

    fireEvent.change(search, { target: { value: "spread" } });
    expect(document.getElementById("spread")).toBeTruthy();
    expect(document.getElementById("cik")).toBeNull();

    fireEvent.change(search, { target: { value: "qwertyuiop" } });
    expect(screen.getByText("No terms match “qwertyuiop”.")).toBeTruthy();
  });

  it("is a nav tab of its own", () => {
    expect(activeNavKey("/glossary")).toBe("/glossary");
  });
});
