import type { SourceCandidate } from '@/services/source-workbench-service';
import { buildSourceTree } from './source-tree';

const article = (document_id: string, path: string[]): SourceCandidate => ({
  dataset_id: 'kb-1',
  document_id,
  title: path[path.length - 1],
  path,
  source_type: 'eva_wiki',
  source_url: null,
  content_hash: '',
  excerpts: [],
});

test('an EVA article can also be the parent of another matching article', () => {
  const tree = buildSourceTree([
    article('parent', ['Проект', 'Раздел']),
    article('child', ['Проект', 'Раздел', 'Статья']),
  ]);
  const project = tree[0].children[0];
  const section = project.children[0];
  expect(section.candidate?.document_id).toBe('parent');
  expect(section.children[0].candidate?.document_id).toBe('child');
});

test('articles with the same EVA path remain separate choices', () => {
  const tree = buildSourceTree([
    article('first', ['Проект', 'Статья']),
    article('second', ['Проект', 'Статья']),
  ]);
  expect(
    tree[0].children[0].children.map((node) => node.candidate?.document_id),
  ).toEqual(['first', 'second']);
});
