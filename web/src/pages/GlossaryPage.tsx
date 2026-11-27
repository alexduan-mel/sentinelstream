import { useEffect, useMemo, useState } from "react";
import { Input, Space, Typography } from "antd";
import { useLocation } from "react-router-dom";

import glossaryMarkdown from "../../../docs/glossary.md?raw";
import { PageTitle, SectionTitle } from "../components/PageTitle";
import { filterGlossary, inlineParts, parseGlossary } from "../lib/glossary";

const GLOSSARY = parseGlossary(glossaryMarkdown);
const TERM_COUNT = GLOSSARY.sections.reduce((count, section) => count + section.terms.length, 0);

function Inline({ text }: { text: string }) {
  return (
    <>
      {inlineParts(text).map((part, index) =>
        part.kind === "bold" ? (
          <strong key={index}>{part.text}</strong>
        ) : part.kind === "code" ? (
          <code key={index} className="font-mono">
            {part.text}
          </code>
        ) : (
          <span key={index}>{part.text}</span>
        )
      )}
    </>
  );
}

/** Plain-language meanings of the terms the app uses, from docs/glossary.md. */
export function GlossaryPage() {
  const [query, setQuery] = useState("");
  const location = useLocation();
  const sections = useMemo(() => filterGlossary(GLOSSARY, query), [query]);

  // /glossary#cik opens at that term.
  useEffect(() => {
    const id = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (id) document.getElementById(id)?.scrollIntoView({ block: "start" });
  }, [location.hash]);

  return (
    <Space orientation="vertical" size={16} className="w-full max-w-[880px]">
      <PageTitle title="Glossary" count={TERM_COUNT} />
      {GLOSSARY.intro.map((text) => (
        <Typography.Paragraph key={text} type="secondary" className="!m-0">
          <Inline text={text} />
        </Typography.Paragraph>
      ))}
      <Input.Search
        aria-label="Search the glossary"
        placeholder="Search, e.g. spread or 8-K"
        allowClear
        className="!w-[320px]"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {sections.length === 0 && (
        <Typography.Text type="secondary">No terms match “{query}”.</Typography.Text>
      )}
      {sections.map((section) => (
        <Space key={section.id} orientation="vertical" size={12} className="w-full">
          <SectionTitle>{section.title}</SectionTitle>
          {section.terms.map((term) => (
            <div key={term.id} id={term.id} className="scroll-mt-64">
              <Typography.Text strong>{term.term}</Typography.Text>
              {term.paragraphs.map((text) => (
                <Typography.Paragraph key={text} className="!mb-0 !mt-4">
                  <Inline text={text} />
                </Typography.Paragraph>
              ))}
            </div>
          ))}
        </Space>
      ))}
    </Space>
  );
}
