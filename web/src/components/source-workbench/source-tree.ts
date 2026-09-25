import type {
  SourceCandidate,
  SourceReference,
} from '@/services/source-workbench-service';

export interface SourceTreeNode {
  key: string;
  label: string;
  candidate?: SourceCandidate;
  children: SourceTreeNode[];
}

export const sourceKey = (source: SourceReference) =>
  `${source.dataset_id}:${source.document_id}`;

/** Render only matching articles while retaining their EVA Wiki ancestor path. */
export function buildSourceTree(
  candidates: SourceCandidate[],
): SourceTreeNode[] {
  const roots: SourceTreeNode[] = [];
  for (const candidate of candidates) {
    const group = `${candidate.source_type}:${candidate.dataset_id}`;
    let parent = roots.find((node) => node.key === group);
    if (!parent) {
      parent = {
        key: group,
        label:
          candidate.source_type === 'eva_wiki' ? 'EVA Wiki' : 'База знаний',
        children: [],
      };
      roots.push(parent);
    }
    const path = candidate.path.length ? candidate.path : [candidate.title];
    path.forEach((part, index) => {
      const final = index === path.length - 1;
      let key = `${parent!.key}:${part}`;
      let child = parent!.children.find((node) => node.key === key);
      if (
        final &&
        child?.candidate &&
        sourceKey(child.candidate) !== sourceKey(candidate)
      ) {
        key = `${key}:${sourceKey(candidate)}`;
        child = parent!.children.find((node) => node.key === key);
      }
      if (!child) {
        child = { key, label: part, children: [] };
        parent!.children.push(child);
      }
      if (final) child.candidate = candidate;
      parent = child;
    });
  }
  const sort = (nodes: SourceTreeNode[]) => {
    nodes.sort((left, right) => left.label.localeCompare(right.label, 'ru'));
    nodes.forEach((node) => sort(node.children));
  };
  sort(roots);
  return roots;
}
