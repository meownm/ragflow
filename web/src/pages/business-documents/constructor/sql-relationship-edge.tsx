import {
  BaseEdge,
  EdgeLabelRenderer,
  type Edge,
  type EdgeProps,
} from '@xyflow/react';

export type RelationshipEdge = Edge<{
  caption: string;
  offset: number;
  onSelect: (id: string) => void;
}>;

export function SqlRelationshipEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  style,
  markerEnd,
  selected,
}: EdgeProps<RelationshipEdge>) {
  const direction = targetX >= sourceX ? 1 : -1;
  const bend = Math.max(70, Math.abs(targetX - sourceX) * 0.45);
  const offset = data?.offset || 0;
  const path = `M ${sourceX},${sourceY} C ${sourceX + direction * bend},${sourceY + offset} ${targetX - direction * bend},${targetY + offset} ${targetX},${targetY}`;
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={selected ? { ...style, strokeWidth: 3, opacity: 1 } : style}
      />
      <EdgeLabelRenderer>
        <button
          type="button"
          className="nodrag nopan rounded border border-border-button bg-bg-base px-1.5 py-0.5 text-[11px] text-text-secondary shadow-sm"
          aria-label={`Связь ${data?.caption}`}
          style={{
            position: 'absolute',
            pointerEvents: 'all',
            transform: `translate(-50%, -50%) translate(${(sourceX + targetX) / 2}px, ${(sourceY + targetY) / 2 + offset * 0.75}px)`,
          }}
          onClick={() => data?.onSelect(id)}
        >
          {data?.caption}
        </button>
      </EdgeLabelRenderer>
    </>
  );
}
