/**
 * Parses docs/glossary.md, the one source for the glossary on GitHub and in the app.
 *
 * Only the format that file uses is understood: "# " title, "## " sections, "### " terms, and
 * paragraphs separated by blank lines, with **bold** and `code` inside. Anything else is rejected,
 * so the page never shows half-rendered Markdown.
 */

export type GlossaryTerm = { id: string; term: string; paragraphs: string[] };
export type GlossarySection = { id: string; title: string; terms: GlossaryTerm[] };
export type Glossary = { intro: string[]; sections: GlossarySection[] };

export function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

const UNSUPPORTED = /^(\s*[-*+]\s|\s*\d+\.\s|>|\||```|#### )|\[[^\]]*\]\(/;

export function parseGlossary(markdown: string): Glossary {
  const glossary: Glossary = { intro: [], sections: [] };
  let section: GlossarySection | null = null;
  let term: GlossaryTerm | null = null;
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length === 0) return;
    const text = paragraph.join(" ");
    paragraph = [];
    if (term) term.paragraphs.push(text);
    else if (!section) glossary.intro.push(text);
    else throw new Error(`Text outside a term in section "${section.title}": ${text.slice(0, 40)}`);
  };

  markdown.split("\n").forEach((raw, index) => {
    const line = raw.trimEnd();
    if (line === "") return flush();
    if (UNSUPPORTED.test(line)) {
      throw new Error(`Line ${index + 1} uses Markdown the glossary page does not render: ${line}`);
    }
    if (line.startsWith("# ")) return flush();
    if (line.startsWith("## ")) {
      flush();
      const title = line.slice(3).trim();
      section = { id: slug(title), title, terms: [] };
      glossary.sections.push(section);
      term = null;
      return undefined;
    }
    if (line.startsWith("### ")) {
      flush();
      if (!section) throw new Error(`Term before any section: ${line}`);
      const name = line.slice(4).trim();
      term = { id: slug(name), term: name, paragraphs: [] };
      section.terms.push(term);
      return undefined;
    }
    paragraph.push(line.trim());
    return undefined;
  });
  flush();
  return glossary;
}

export type InlinePart = { kind: "text" | "bold" | "code"; text: string };

/** Splits a paragraph into plain, **bold** and `code` runs. */
export function inlineParts(text: string): InlinePart[] {
  return text
    .split(/(\*\*[^*]+\*\*|`[^`]+`)/)
    .filter(Boolean)
    .map((part) =>
      part.startsWith("**") && part.endsWith("**") && part.length > 4
        ? { kind: "bold" as const, text: part.slice(2, -2) }
        : part.startsWith("`") && part.endsWith("`") && part.length > 2
          ? { kind: "code" as const, text: part.slice(1, -1) }
          : { kind: "text" as const, text: part }
    );
}

/** Terms whose name or explanation mentions every word of ``query``, sections without any dropped. */
export function filterGlossary(glossary: Glossary, query: string): GlossarySection[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return glossary.sections;
  return glossary.sections
    .map((section) => ({
      ...section,
      terms: section.terms.filter((term) => {
        const haystack = `${term.term} ${term.paragraphs.join(" ")}`.toLowerCase();
        return words.every((word) => haystack.includes(word));
      })
    }))
    .filter((section) => section.terms.length > 0);
}
