import { RAGFlowNodeType } from '@/interfaces/database/agent';
import { renderHook } from '@testing-library/react';
import { Edge } from '@xyflow/react';
import { NodeHandleId, Operator } from '../constant';
import useGraphStore from '../store';
import { useBeforeDelete } from './use-before-delete';

const createNode = (
  id: string,
  label: Operator,
  options: Partial<RAGFlowNodeType> = {},
): RAGFlowNodeType =>
  ({
    id,
    type: 'ragNode',
    position: { x: 0, y: 0 },
    data: {
      label,
      name: id,
      form: {},
    },
    ...options,
  }) as RAGFlowNodeType;

const createEdge = (
  id: string,
  source: string,
  target: string,
  options: Partial<Edge> = {},
): Edge => ({
  id,
  source,
  target,
  ...options,
});

describe('useBeforeDelete', () => {
  beforeEach(() => {
    useGraphStore.setState({
      nodes: [],
      edges: [],
      selectedNodeIds: [],
      selectedEdgeIds: [],
      clickedNodeId: '',
      clickedToolId: '',
    });
  });

  it('expands iteration deletion to descendants and all touching edges', async () => {
    const nodes = [
      createNode('iteration:0', Operator.Iteration, { type: 'group' }),
      createNode('iterationStart:0', Operator.IterationStart, {
        parentId: 'iteration:0',
        type: 'iterationStartNode',
      }),
      createNode('message:0', Operator.Message, { parentId: 'iteration:0' }),
      createNode('message:1', Operator.Message, { parentId: 'message:0' }),
      createNode('rewrite:0', Operator.RewriteQuestion),
    ];

    const edges = [
      createEdge('e1', 'iterationStart:0', 'message:0'),
      createEdge('e2', 'message:0', 'message:1'),
      createEdge('e3', 'message:0', 'rewrite:0'),
      createEdge('e4', 'rewrite:0', 'message:1'),
    ];

    useGraphStore.setState({ nodes, edges });

    const { result } = renderHook(() => useBeforeDelete());
    const deletion = (await result.current.handleBeforeDelete({
      nodes: [nodes[0]],
      edges: [],
    })) as { nodes: RAGFlowNodeType[]; edges: Edge[] };

    expect(deletion?.nodes.map((node) => node.id).sort()).toEqual(
      ['iteration:0', 'iterationStart:0', 'message:0', 'message:1'].sort(),
    );
    expect(deletion?.edges.map((edge) => edge.id).sort()).toEqual(
      ['e1', 'e2', 'e3', 'e4'].sort(),
    );
  });

  it('keeps begin and detached iteration-start protected', async () => {
    const beginNode = createNode('begin', Operator.Begin);
    const iterationNode = createNode('iteration:0', Operator.Iteration, {
      type: 'group',
    });
    const iterationStartNode = createNode(
      'iterationStart:0',
      Operator.IterationStart,
      {
        parentId: 'iteration:0',
        type: 'iterationStartNode',
      },
    );

    useGraphStore.setState({
      nodes: [beginNode, iterationNode, iterationStartNode],
      edges: [],
    });

    const { result } = renderHook(() => useBeforeDelete());
    const beginDeletion = (await result.current.handleBeforeDelete({
      nodes: [beginNode],
      edges: [],
    })) as { nodes: RAGFlowNodeType[]; edges: Edge[] };
    const startDeletion = (await result.current.handleBeforeDelete({
      nodes: [iterationStartNode],
      edges: [],
    })) as { nodes: RAGFlowNodeType[]; edges: Edge[] };

    expect(beginDeletion?.nodes).toEqual([]);
    expect(startDeletion?.nodes).toEqual([]);
  });

  it('preserves agent downstream cleanup', async () => {
    const nodes = [
      createNode('agent:0', Operator.Agent),
      createNode('tool:0', Operator.Tool),
      createNode('message:0', Operator.Message),
    ];

    const edges = [
      createEdge('e1', 'agent:0', 'tool:0', {
        sourceHandle: NodeHandleId.AgentBottom,
      }),
      createEdge('e2', 'tool:0', 'message:0', {
        sourceHandle: NodeHandleId.Tool,
      }),
    ];

    useGraphStore.setState({ nodes, edges });

    const { result } = renderHook(() => useBeforeDelete());
    const deletion = (await result.current.handleBeforeDelete({
      nodes: [nodes[0]],
      edges,
    })) as { nodes: RAGFlowNodeType[]; edges: Edge[] };

    expect(deletion?.nodes.map((node) => node.id).sort()).toEqual(
      ['agent:0', 'tool:0', 'message:0'].sort(),
    );
    expect(deletion?.edges.map((edge) => edge.id).sort()).toEqual(
      ['e1', 'e2'].sort(),
    );
  });

  it('expands iteration deletion to nested agent tool chains', async () => {
    const nodes = [
      createNode('iteration:0', Operator.Iteration, { type: 'group' }),
      createNode('iterationStart:0', Operator.IterationStart, {
        parentId: 'iteration:0',
        type: 'iterationStartNode',
      }),
      createNode('agent:0', Operator.Agent, { parentId: 'iteration:0' }),
      createNode('tool:0', Operator.Tool),
      createNode('message:0', Operator.Message),
      createNode('rewrite:0', Operator.RewriteQuestion),
    ];

    const edges = [
      createEdge('e1', 'iterationStart:0', 'agent:0'),
      createEdge('e2', 'agent:0', 'tool:0', {
        sourceHandle: NodeHandleId.AgentBottom,
      }),
      createEdge('e3', 'tool:0', 'message:0', {
        sourceHandle: NodeHandleId.Tool,
      }),
      createEdge('e4', 'rewrite:0', 'message:0'),
    ];

    useGraphStore.setState({ nodes, edges });

    const { result } = renderHook(() => useBeforeDelete());
    const deletion = (await result.current.handleBeforeDelete({
      nodes: [nodes[0]],
      edges: [],
    })) as { nodes: RAGFlowNodeType[]; edges: Edge[] };

    expect(deletion?.nodes.map((node) => node.id).sort()).toEqual(
      [
        'iteration:0',
        'iterationStart:0',
        'agent:0',
        'tool:0',
        'message:0',
      ].sort(),
    );
    expect(deletion?.edges.map((edge) => edge.id).sort()).toEqual(
      ['e1', 'e2', 'e3', 'e4'].sort(),
    );
  });
});
