import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type {
  CohortData,
  OrgNodeData,
  PeerMoreData,
  PersonNodeData,
} from "../lib/graphLayout";
import { RANK_LABELS, rankLabel, type PositionRank } from "../lib/positionRank";

// -----------------------------------------------------------------------------
// Custom React Flow node types
//
// Each node exports one hidden target handle on top and one source handle on
// bottom, which is what makes dagre's TB rankdir produce orthogonal, well-
// routed edges. Interactivity (clicks, buttons) sits inside the card body.
// -----------------------------------------------------------------------------

function fadeStyle(fade: number): React.CSSProperties {
  return fade > 0 ? { opacity: 1 - fade } : {};
}

/** Coarse "how long is this label" ladder that maps 1:1 to CSS rules that
 *  shrink the font. Kept short so the card never grows past its fixed
 *  height — dagre reserves exactly the size CSS paints, so if the font
 *  didn't shrink here we'd overflow and clip. */
function nameLenTier(s: string): "s" | "l" | "xl" {
  const n = s.length;
  if (n <= 18) return "s";
  if (n <= 28) return "l";
  return "xl";
}

const PersonNodeImpl = ({ data }: NodeProps) => {
  const d = data as unknown as PersonNodeData;
  const className = [
    "graph-node",
    "graph-node--person",
    d.selected ? "is-selected" : "",
    d.onPath ? "is-on-path" : "",
    d.stub ? "is-stub" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={className} style={fadeStyle(d.fade)} onClick={d.onSelect}>
      <Handle type="target" position={Position.Top} className="graph-handle" isConnectable={false} />
      <div className="graph-node__body">
        <div className="graph-node__name" data-len={nameLenTier(d.name)}>
          {d.name}
        </div>
        {d.role && <div className="graph-node__role">{d.role}</div>}
        {d.institution && (
          <div className="graph-node__inst">{d.institution}</div>
        )}
      </div>
      {d.loading && <span className="graph-node__spinner" aria-hidden="true" />}
      <Handle type="source" position={Position.Bottom} className="graph-handle" isConnectable={false} />
    </div>
  );
};

const PeerMoreImpl = ({ data }: NodeProps) => {
  const d = data as unknown as PeerMoreData;
  return (
    <button
      type="button"
      className="graph-peer"
      style={fadeStyle(d.fade)}
      onClick={(e) => {
        e.stopPropagation();
        d.onOpen();
      }}
      aria-label={`Show ${d.count} more colleagues`}
      title={`Show ${d.count} more colleagues`}
    >
      <Handle type="target" position={Position.Top} className="graph-handle" isConnectable={false} />
      <span className="graph-peer__count">+{d.count}</span>
    </button>
  );
};

const OrgNodeImpl = ({ data }: NodeProps) => {
  const d = data as unknown as OrgNodeData;
  const className = [
    "graph-node",
    "graph-node--org",
    d.orgKind ? `graph-node--org-${d.orgKind}` : "",
    d.selected ? "is-selected" : "",
    d.onPath ? "is-on-path" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={className} style={fadeStyle(d.fade)} onClick={d.onSelect}>
      <Handle type="target" position={Position.Top} className="graph-handle" isConnectable={false} />
      <div className="graph-node__body">
        <div className="graph-node__name" data-len={nameLenTier(d.name)}>
          {d.name}
        </div>
        {d.sublabel && <div className="graph-node__role">{d.sublabel}</div>}
      </div>
      {d.loading && <span className="graph-node__spinner" aria-hidden="true" />}
      <Handle type="source" position={Position.Bottom} className="graph-handle" isConnectable={false} />
    </div>
  );
};

const CohortNodeImpl = ({ data }: NodeProps) => {
  const d = data as unknown as CohortData;
  const showFilter = d.ranks.length >= 2;

  return (
    <div className="graph-cohort" style={fadeStyle(d.fade)}>
      <Handle type="target" position={Position.Top} className="graph-handle" isConnectable={false} />
      <div className="graph-cohort__header">
        <div>
          <div className="graph-cohort__title">
            {d.groupLabel}{" "}
            <span className="graph-cohort__count">
              · {d.count} in {d.parentName}
            </span>
          </div>
        </div>
        <button
          type="button"
          className="graph-cohort__close"
          onClick={(e) => {
            e.stopPropagation();
            d.onToggle();
          }}
        >
          Close
        </button>
      </div>

      {showFilter && (
        <div className="graph-cohort__filter">
          <button
            type="button"
            className={d.rankFilter == null ? "is-active" : ""}
            onClick={(e) => {
              e.stopPropagation();
              d.onFilter(undefined);
            }}
          >
            All
          </button>
          {RANK_LABELS.filter((entry) => d.ranks.includes(entry.rank)).map(
            (entry) => (
              <button
                key={entry.rank}
                type="button"
                className={d.rankFilter === entry.rank ? "is-active" : ""}
                onClick={(e) => {
                  e.stopPropagation();
                  d.onFilter(
                    d.rankFilter === entry.rank ? undefined : entry.rank,
                  );
                }}
              >
                {entry.label}
              </button>
            ),
          )}
        </div>
      )}

      <div className="graph-cohort__grid">
        {d.people.map((p) => (
          <button
            key={p.id}
            type="button"
            className={[
              "graph-cohort__item",
              p.selected ? "is-selected" : "",
              p.onCanvas ? "is-on-canvas" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            onClick={(e) => {
              e.stopPropagation();
              d.onPersonSelect(p.id);
            }}
          >
            <span className="graph-cohort__name">{p.name}</span>
            {p.rank && (
              <span className="graph-cohort__rank">
                {rankLabel(p.rank as PositionRank)}
              </span>
            )}
          </button>
        ))}
      </div>

      {d.remaining > 0 && (
        <div className="graph-cohort__footer">
          <button
            type="button"
            className="graph-cohort__more"
            onClick={(e) => {
              e.stopPropagation();
              d.onLoadMore();
            }}
          >
            Load more · {d.remaining} left
          </button>
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="graph-handle" isConnectable={false} />
    </div>
  );
};

export const PersonNode = memo(PersonNodeImpl);
export const OrgNode = memo(OrgNodeImpl);
export const CohortNode = memo(CohortNodeImpl);
export const PeerMore = memo(PeerMoreImpl);

export const nodeTypes = {
  person: PersonNode,
  org: OrgNode,
  cohort: CohortNode,
  peer_more: PeerMore,
};
