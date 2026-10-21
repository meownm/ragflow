import authorization, { AUTHORIZATION_CHANGED } from './authorization-util';

describe('authorization state notifications', () => {
  beforeEach(() => localStorage.clear());

  it.each([
    () => authorization.setAuthorization('Bearer synthetic'),
    () => authorization.setToken('synthetic'),
    () => authorization.setUserInfo({ id: 'synthetic' }),
    () => authorization.removeAuthorization(),
    () => authorization.removeAll(),
  ])(
    'notifies after an authorization mutation without sending credential data',
    (mutate) => {
      const listener = jest.fn();
      window.addEventListener(AUTHORIZATION_CHANGED, listener);
      try {
        mutate();
        expect(listener).toHaveBeenCalledTimes(1);
        expect(listener.mock.calls[0][0]).toBeInstanceOf(Event);
        expect(listener.mock.calls[0][0]).not.toHaveProperty('detail');
      } finally {
        window.removeEventListener(AUTHORIZATION_CHANGED, listener);
      }
    },
  );

  it('publishes one event after the entire login session is stored', () => {
    const snapshots: Array<Array<string | null>> = [];
    const listener = () =>
      snapshots.push([
        authorization.getAuthorization(),
        authorization.getToken(),
        authorization.getUserInfo(),
      ]);
    window.addEventListener(AUTHORIZATION_CHANGED, listener);
    try {
      authorization.setItems({
        Authorization: 'Bearer synthetic',
        token: 'synthetic',
        userInfo: '{"id":"a"}',
      });
      expect(snapshots).toEqual([
        ['Bearer synthetic', 'synthetic', '{"id":"a"}'],
      ]);
      authorization.setLanguage('en');
      authorization.setItems({ lng: 'ru' });
      expect(snapshots).toHaveLength(1);
    } finally {
      window.removeEventListener(AUTHORIZATION_CHANGED, listener);
    }
  });
});
