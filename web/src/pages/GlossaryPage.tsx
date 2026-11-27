import { Fragment, useEffect, useMemo, useState } from "react";
import { Input, Space, Typography } from "antd";
import { Link, useLocation } from "react-router-dom";

import glossaryMarkdown from "../../../docs/glossary.md?raw";
import { PageTitle } from "../components/PageTitle";
import {
  filterGlossary,
  inlineParts,
  parseGlossary,
  type GlossarySection,
  type GlossaryTerm
} from "../lib/glossary";

const GLOSSARY = parseGlossary(glossaryMarkdown);
const TERM_COUNT = GLOSSARY.sections.reduce((count, section) => count + section.terms.length, 0);
/** How far below the top of the window a section counts as the one being read. */
const READING_LINE_PX = 96;

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

/** The section whose heading has most recently scrolled past the reading line. */
function useSectionInView(sections: GlossarySection[]): string | undefined {
  const [current, setCurrent] = useState<string | undefined>(sections[0]?.id);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      let inView = sections[0]?.id;
      for (const section of sections) {
        const element = document.getElementById(section.id);
        if (element && element.getBoundingClientRect().top <= READING_LINE_PX) inView = section.id;
      }
      setCurrent(inView);
    };
    const onScroll = () => {
      if (!frame) frame = window.requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [sections]);
  return current;
}

/** One term as a dictionary row: name (and what it stands for) on the left, meaning on the right. */
function TermRow({ term, onFollow }: { term: GlossaryTerm; onFollow: () => void }) {
  return (
    <div
      id={term.id}
      className="grid scroll-mt-24 grid-cols-[200px_minmax(0,1fr)] gap-x-24 border-0 border-t border-solid border-border-default py-12"
    >
      <div>
        <Typography.Text strong>{term.term}</Typography.Text>
        {term.shortFor && (
          <div>
            <Typography.Text type="secondary" className="text-xs">
              {term.shortFor}
            </Typography.Text>
          </div>
        )}
      </div>
      <Space orientation="vertical" size={8} className="w-full">
        {term.paragraphs.map((text) => (
          <Typography.Paragraph key={text} className="!m-0">
            <Inline text={text} />
          </Typography.Paragraph>
        ))}
        {term.example && (
          <Typography.Paragraph type="secondary" className="!m-0">
            <span className="font-medium">Example: </span>
            <Inline text={term.example} />
          </Typography.Paragraph>
        )}
        {term.seeAlso.length > 0 && (
          <Typography.Text type="secondary">
            See also:{" "}
            {term.seeAlso.map((other, index) => (
              <Fragment key={other.id}>
                {index > 0 && " · "}
                <Link to={`#${other.id}`} onClick={onFollow}>
                  {other.term}
                </Link>
              </Fragment>
            ))}
          </Typography.Text>
        )}
      </Space>
    </div>
  );
}

/** Plain-language meanings of the terms the app uses, from docs/glossary.md. DESIGN.md, Glossary. */
export function GlossaryPage() {
  const [query, setQuery] = useState("");
  const location = useLocation();
  const sections = useMemo(() => filterGlossary(GLOSSARY, query), [query]);
  const shown = sections.reduce((count, section) => count + section.terms.length, 0);
  const inView = useSectionInView(sections);

  // /glossary#cik opens at that term; the topic list and "See also" links use the same anchors.
  useEffect(() => {
    const id = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (id) document.getElementById(id)?.scrollIntoView({ block: "start" });
  }, [location.hash]);

  return (
    <Space orientation="vertical" size={16} className="w-full">
      <PageTitle title="Glossary" count={TERM_COUNT} />
      {GLOSSARY.intro.map((text) => (
        <Typography.Paragraph key={text} type="secondary" className="!m-0 max-w-[880px]">
          <Inline text={text} />
        </Typography.Paragraph>
      ))}
      <Space size={12}>
        <Input.Search
          aria-label="Search the glossary"
          placeholder="Search, e.g. spread or 8-K"
          allowClear
          className="!w-[320px]"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {query.trim() && (
          <Typography.Text type="secondary">
            {shown} of {TERM_COUNT} terms
          </Typography.Text>
        )}
      </Space>

      <div className="flex items-start gap-32">
        <nav aria-label="Glossary topics" className="sticky top-24 w-[200px] shrink-0">
          {sections.map((section) => {
            const active = section.id === inView;
            return (
              <Link
                key={section.id}
                to={`#${section.id}`}
                aria-current={active ? "location" : undefined}
                className={`flex justify-between border-0 border-l-2 border-solid py-4 pl-12 pr-8 ${
                  active
                    ? "border-semantic-info text-text-primary font-medium"
                    : "border-transparent text-text-secondary"
                }`}
              >
                <span>{section.title}</span>
                <span className="text-text-muted">{section.terms.length}</span>
              </Link>
            );
          })}
        </nav>

        <div className="min-w-0 max-w-[960px] flex-1">
          {sections.length === 0 && (
            <Typography.Text type="secondary">No terms match “{query}”.</Typography.Text>
          )}
          {sections.map((section) => (
            <section key={section.id} id={section.id} className="mb-32 scroll-mt-24">
              <Typography.Title level={2} className="!mb-8 !mt-0 !text-section-title">
                {section.title}
              </Typography.Title>
              {section.terms.map((term) => (
                // Following a link clears the search, so a term it had hidden is there to scroll to.
                <TermRow key={term.id} term={term} onFollow={() => setQuery("")} />
              ))}
            </section>
          ))}
        </div>
      </div>
    </Space>
  );
}
