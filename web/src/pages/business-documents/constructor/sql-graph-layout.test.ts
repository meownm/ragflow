import type { SqlQueryGraph } from '@/pages/business-documents/types';
import { graphPositions } from './sql-graph-layout';

function table(
  id: string,
  membership: 'QUERY' | 'RELATED' = 'RELATED',
  scope: string | null = null,
): SqlQueryGraph['nodes'][number] {
  return {
    id,
    entity_id: id,
    alias: id,
    label: id,
    kind: 'TABLE',
    membership,
    scope,
    schema: 'public',
    technical_name: id,
    columns: [],
  };
}

function edge(
  id: string,
  from: string,
  to: string,
): SqlQueryGraph['edges'][number] {
  return {
    id,
    from,
    to,
    kind: 'FOREIGN_KEY',
    join_type: '',
    condition: '',
    source: 'OpenMetadata',
    cardinality: 'UNKNOWN',
    column_pairs: [],
  };
}

it('places neighbors on the next level without shrinking three tables into one row', () => {
  const positions = graphPositions({
    nodes: [
      table('orders', 'QUERY', 'root'),
      table('customers'),
      table('items'),
    ],
    edges: [
      edge('customer', 'orders', 'customers'),
      edge('items', 'items', 'orders'),
    ],
  });
  expect(positions.orders.x).toBeLessThan(positions.customers.x);
  expect(positions.customers.x).toBe(positions.items.x);
  expect(
    Math.abs(positions.customers.y - positions.items.y),
  ).toBeGreaterThanOrEqual(280);
});

it('finds the main query despite nested scopes and duplicate FK edges', () => {
  const graph = {
    nodes: [
      table('nested', 'QUERY', 'root/subquery:0'),
      table('orders', 'QUERY', 'root'),
      table('customers'),
    ],
    edges: [
      edge('a', 'orders', 'customers'),
      edge('b', 'orders', 'customers'),
      edge('c', 'customers', 'nested'),
    ],
  };
  const positions = graphPositions(graph);
  expect(positions.orders.x).toBe(0);
  expect(positions.customers.x).toBe(380);
  expect(positions.nested.x).toBe(760);
  expect(graphPositions(graph)).toEqual(positions);
});

it('terminates with cycles and preserves a distinct position for every table', () => {
  const nodes = Array.from({ length: 100 }, (_, index) =>
    table(
      `table${index}`,
      index === 0 ? 'QUERY' : 'RELATED',
      index === 0 ? 'root' : null,
    ),
  );
  const edges = nodes.map((node, index) =>
    edge(String(index), node.id, nodes[(index + 1) % nodes.length].id),
  );
  const positions = graphPositions({ nodes, edges });
  expect(Object.keys(positions)).toHaveLength(100);
  expect(
    new Set(Object.values(positions).map((point) => `${point.x}:${point.y}`))
      .size,
  ).toBe(100);
});
