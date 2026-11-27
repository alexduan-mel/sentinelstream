/**
 * Parses docs/glossary.md, the one source for the glossary on GitHub and in the app.
 *
 * Only the format that file uses is understood: "# " title, "## " sections, "### " terms, and
 * paragraphs separated by blank lines, with **bold** and `code` inside. Anything else is rejected,
 * so the page never shows half-rendered Markdown.
 *
 * Three kinds of paragraph under a term are read as fields, and read naturally on GitHub too:
 *   **Short for:** Central Index Key        what an abbreviation stands for
 *   **Example:** ...                         one concrete example
 *   **See also:** CIK; Ticker                related terms, by name, separated by semicolons
 * Every "See also" name must be a term in the file.
 */

export type SeeAlso = { id: string; term: string };
export type GlossaryTerm = {
  id: string;
  term: string;
  shortFor?: string;
  paragraphs: string[];
  example?: string;
  seeAlso: SeeAlso[];
};

const SHORT_FOR = "**Short for:** ";
const EXAMPLE = "**Example:** ";
const SEE_ALSO = "**See also:** ";
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
  const seeAlsoNames = new Map<GlossaryTerm, string[]>();

  const flush = () => {
    if (paragraph.length === 0) return;
    const text = paragraph.join(" ");
    paragraph = [];
    if (term && text.startsWith(SHORT_FOR)) term.shortFor = text.slice(SHORT_FOR.length).trim();
    else if (term && text.startsWith(EXAMPLE)) term.example = text.slice(EXAMPLE.length).trim();
    else if (term && text.startsWith(SEE_ALSO)) {
      const names = text
        .slice(SEE_ALSO.length)
        .split(";")
        .map((name) => name.trim().replace(/\.$/, ""))
        .filter(Boolean);
      seeAlsoNames.set(term, names);
    } else if (term) term.paragraphs.push(text);
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
      term = { id: slug(name), term: name, paragraphs: [], seeAlso: [] };
      section.terms.push(term);
      return undefined;
    }
    paragraph.push(line.trim());
    return undefined;
  });
  flush();

  const byName = new Map(
    glossary.sections.flatMap((s) => s.terms).map((t) => [t.term.toLowerCase(), t] as const)
  );
  for (const [owner, names] of seeAlsoNames) {
    owner.seeAlso = names.map((name) => {
      const target = byName.get(name.toLowerCase());
      if (!target) throw new Error(`"See also" under "${owner.term}" names no term: ${name}`);
      if (target === owner) throw new Error(`"See also" under "${owner.term}" names itself`);
      return { id: target.id, term: target.term };
    });
  }
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

/** Terms whose name, expansion, explanation or example mentions every word of ``query``; sections
 *  left without terms are dropped. */
export function filterGlossary(glossary: Glossary, query: string): GlossarySection[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return glossary.sections;
  return glossary.sections
    .map((section) => ({
      ...section,
      terms: section.terms.filter((term) => {
        const haystack = [term.term, term.shortFor, ...term.paragraphs, term.example]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return words.every((word) => haystack.includes(word));
      })
    }))
    .filter((section) => section.terms.length > 0);
}
