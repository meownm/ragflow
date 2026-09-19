import { RAGFlowNodeType } from '@/interfaces/database/agent';
import { Edge } from '@xyflow/react';
import { NodeHandleId, Operator, SwitchElseTo } from './constant';
import useGraphStore from './store';
import { buildDslComponentsByGraph } from './utils';

function baseNode(id: string, label: Operator): RAGFlowNodeType {
  return {
    id,
    type: 'ragNode',
    position: { x: 0, y: 0 },
    data: {
      label,
      name: id,
      form: {},
    },
  } as RAGFlowNodeType;
}

const createNode = (
  id: string,
  label: Operator,
  options: Partial<RAGFlowNodeType> = {},
): RAGFlowNodeType =>
  ({
    ...baseNode(id, label),
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

describe('useGraphStore.deleteIterationNodeById', () => {
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

  it('removes the iteration node, its descendants, and all incident edges', () => {
    const nodes = [
      createNode('begin', Operator.Begin),
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
      createEdge('e1', 'begin', 'iteration:0'),
      createEdge('e2', 'iterationStart:0', 'message:0'),
      createEdge('e3', 'message:0', 'message:1'),
      createEdge('e4', 'message:0', 'rewrite:0'),
      createEdge('e5', 'rewrite:0', 'message:1'),
    ];

    useGraphStore.setState({
      nodes,
      edges,
      selectedNodeIds: ['iteration:0', 'message:0'],
      selectedEdgeIds: ['e2', 'e4'],
      clickedNodeId: 'message:0',
    });

    useGraphStore.getState().deleteIterationNodeById('iteration:0');

    const state = useGraphStore.getState();

    expect(state.nodes.map((node) => node.id)).toEqual(['begin', 'rewrite:0']);
    expect(state.edges.map((edge) => edge.id)).toEqual([]);
    expect(state.selectedNodeIds).toEqual([]);
    expect(state.selectedEdgeIds).toEqual([]);
    expect(state.clickedNodeId).toBe('');
  });

  it('preserves unrelated graph branches', () => {
    const nodes = [
      createNode('iteration:0', Operator.Iteration, { type: 'group' }),
      createNode('iterationStart:0', Operator.IterationStart, {
        parentId: 'iteration:0',
        type: 'iterationStartNode',
      }),
      createNode('message:0', Operator.Message, { parentId: 'iteration:0' }),
      createNode('begin', Operator.Begin),
      createNode('rewrite:0', Operator.RewriteQuestion),
      createNode('message:2', Operator.Message),
    ];

    const edges = [
      createEdge('iteration-edge', 'iterationStart:0', 'message:0'),
      createEdge('branch-edge-a', 'begin', 'rewrite:0'),
      createEdge('branch-edge-b', 'rewrite:0', 'message:2'),
    ];

    useGraphStore.setState({ nodes, edges });

    useGraphStore.getState().deleteIterationNodeById('iteration:0');

    const state = useGraphStore.getState();

    expect(state.nodes.map((node) => node.id)).toEqual([
      'begin',
      'rewrite:0',
      'message:2',
    ]);
    expect(state.edges.map((edge) => edge.id)).toEqual([
      'branch-edge-a',
      'branch-edge-b',
    ]);
  });

  it('removes agent tool chains nested inside an iteration subtree', () => {
    const nodes = [
      createNode('iteration:0', Operator.Iteration, { type: 'group' }),
      createNode('iterationStart:0', Operator.IterationStart, {
        parentId: 'iteration:0',
        type: 'iterationStartNode',
      }),
      createNode('agent:0', Operator.Agent, { parentId: 'iteration:0' }),
      createNode('tool:0', Operator.Tool),
      createNode('message:0', Operator.Message),
      createNode('begin', Operator.Begin),
      createNode('rewrite:0', Operator.RewriteQuestion),
    ];

    const edges = [
      createEdge('iteration-edge', 'iterationStart:0', 'agent:0'),
      createEdge('tool-edge', 'agent:0', 'tool:0', {
        sourceHandle: NodeHandleId.AgentBottom,
      }),
      createEdge('tool-output-edge', 'tool:0', 'message:0', {
        sourceHandle: NodeHandleId.Tool,
      }),
      createEdge('branch-edge', 'begin', 'rewrite:0'),
    ];

    useGraphStore.setState({ nodes, edges });

    useGraphStore.getState().deleteIterationNodeById('iteration:0');

    const state = useGraphStore.getState();

    expect(state.nodes.map((node) => node.id)).toEqual(['begin', 'rewrite:0']);
    expect(state.edges.map((edge) => edge.id)).toEqual(['branch-edge']);
  });
});

describe('buildDslComponentsByGraph', () => {
  it('rebuilds switch destinations from graph edges', () => {
    const switchId = 'Switch:switch-1';
    const caseTarget = 'Message:message-1';
    const elseTarget = 'Agent:agent-1';
    const nodes = [
      createNode(switchId, Operator.Switch, {
        data: {
          label: Operator.Switch,
          name: 'Switch_0',
          form: {
            conditions: [
              {
                items: [{ cpn_id: 'sys.query', operator: 'empty' }],
                logical_operator: 'or',
                to: [],
              },
            ],
            end_cpn_ids: [],
          },
        },
      }),
      createNode(caseTarget, Operator.Message),
      createNode(elseTarget, Operator.Agent, {
        data: {
          label: Operator.Agent,
          name: 'Agent_0',
          form: { tools: [] },
        },
      }),
    ];
    const edges = [
      createEdge('case-edge', switchId, caseTarget, {
        sourceHandle: 'Case 1',
      }),
      createEdge('else-edge', switchId, elseTarget, {
        sourceHandle: SwitchElseTo,
      }),
    ];

    const components = buildDslComponentsByGraph(nodes, edges, {});
    const switchParams = components[switchId].obj.params as any;

    expect(switchParams.conditions[0].to).toEqual([caseTarget]);
    expect(switchParams.end_cpn_ids).toEqual([elseTarget]);
  });
});
