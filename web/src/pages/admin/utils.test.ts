import { formMergeDefaultValues } from './utils';

describe('formMergeDefaultValues', () => {
  it('merges synchronous and asynchronous defaults while ignoring undefined parts', async () => {
    const defaults = formMergeDefaultValues<{ name: string; enabled: boolean }>(
      { name: 'initial', enabled: false },
      undefined,
      async (payload) => ({
        name: 'diagram',
        enabled: payload === 'enabled',
      }),
    );

    await expect(defaults('enabled')).resolves.toEqual({
      name: 'diagram',
      enabled: true,
    });
  });
});
