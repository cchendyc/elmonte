import { useState } from "react";

interface Props {
  text: string;
  className?: string;
  maxChars?: number;
}

function truncateAtWord(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  const slice = text.slice(0, maxChars);
  const lastSpace = slice.lastIndexOf(" ");
  const trimmed = (lastSpace > 40 ? slice.slice(0, lastSpace) : slice).trimEnd();
  return `${trimmed}…`;
}

export function ExpandableText({ text, className, maxChars = 220 }: Props) {
  const [expanded, setExpanded] = useState(false);
  const canExpand = text.length > maxChars;
  const displayText = expanded || !canExpand ? text : truncateAtWord(text, maxChars);

  return (
    <div className="expandable-text">
      <p className={className}>{displayText}</p>
      {canExpand && (
        <button
          type="button"
          className="expandable-text__toggle"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}
