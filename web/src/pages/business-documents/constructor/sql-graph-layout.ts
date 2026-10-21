import type { SqlQueryGraph } from '@/pages/business-documents/types';

export function graphPositions(graph: Pick<SqlQueryGraph, 'nodes' | 'edges'>) {
  const adjacency = new Map<string, Set<string>>();
  for (const edge of graph.edges) {
    if (!adjacency.has(edge.from)) adjacency.set(edge.from, new Set());
    if (!adjacency.has(edge.to)) adjacency.set(edge.to, new Set());
    adjacency.get(edge.from)!.add(edge.to);
    adjacency.get(edge.to)!.add(edge.from);
  }
  const root =
    graph.nodes.find(
      (node) => node.membership === 'QUERY' && node.scope === 'root',
    ) ||
    graph.nodes.find((node) => node.membership === 'QUERY') ||
    graph.nodes[0];
  const depths = new Map<string, number>();
  const queue = root ? [root.id] : [];
  if (root) depths.set(root.id, 0);
  for (let index = 0; index < queue.length; index += 1) {
    const id = queue[index];
    for (const neighbor of adjacency.get(id) || []) {
      if (depths.has(neighbor)) continue;
      depths.set(neighbor, depths.get(id)! + 1);
      queue.push(neighbor);
    }
  }
  const columns = new Map<number, SqlQueryGraph['nodes']>();
  for (const node of graph.nodes) {
    const depth = depths.get(node.id) ?? 0;
    columns.set(depth, [...(columns.get(depth) || []), node]);
  }
  return Object.fromEntries(
    [...columns].flatMap(([depth, nodes]) =>
      nodes.map((node, index) => [
        node.id,
        { x: depth * 380, y: (index - (nodes.length - 1) / 2) * 280 },
      ]),
    ),
  );
}
