import api from '@/utils/api';
import evaRequest from '@/utils/next-request';
import registerServer from '@/utils/register-server';
import request, { post } from '@/utils/request';
import type { AxiosRequestConfig } from 'axios';

const {
  login,
  logout,
  register,
  setting,
  userInfo,
  tenantInfo,
  getSystemVersion,
  getSystemTokenList,
  removeSystemToken,
  createSystemToken,
  getSystemConfig,
  setLangfuseConfig,
} = api;

const methods = {
  login: {
    url: login,
    method: 'post',
  },
  logout: {
    url: logout,
    method: 'post',
  },
  register: {
    url: register,
    method: 'post',
  },
  setting: {
    url: setting,
    method: 'patch',
  },
  userInfo: {
    url: userInfo,
    method: 'get',
  },
  getTenantInfo: {
    url: tenantInfo,
    method: 'get',
  },
  getSystemVersion: {
    url: getSystemVersion,
    method: 'get',
  },
  listToken: {
    url: getSystemTokenList,
    method: 'get',
  },
  createToken: {
    url: createSystemToken,
    method: 'post',
  },
  removeToken: {
    url: removeSystemToken,
    method: 'delete',
  },
  getSystemConfig: {
    url: getSystemConfig,
    method: 'get',
  },
  setLangfuseConfig: {
    url: setLangfuseConfig,
    method: 'put',
  },
  getLangfuseConfig: {
    url: setLangfuseConfig,
    method: 'get',
  },
  deleteLangfuseConfig: {
    url: setLangfuseConfig,
    method: 'delete',
  },
} as const;

const userService = registerServer<keyof typeof methods>(methods, request);

export const getLoginChannels = () => request.get(api.loginChannels);
export const loginWithChannel = (channel: string) =>
  (window.location.href = api.loginChannel(channel));

export const listTenantUser = (tenantId: string) =>
  request.get(api.listTenantUser(tenantId));

export const addTenantUser = (tenantId: string, email: string) =>
  post(api.addTenantUser(tenantId), { email });

export const deleteTenantUser = ({
  tenantId,
  userId,
}: {
  tenantId: string;
  userId: string;
}) =>
  request.delete(api.deleteTenantUser(tenantId), {
    data: { userId },
  });

export const listTenant = () => request.get(api.listTenant);

export const agreeTenant = (tenantId: string) =>
  request.patch(api.agreeTenant(tenantId));

export interface EvaUserCredentialStatus {
  scope: string;
  configured: boolean;
  credential_version?: number | null;
  update_time?: number | null;
  connector_id: string;
  connectors: Array<{ id: string; name: string }>;
}

const evaCredentialConfig = {
  skipErrorNotification: true,
} as AxiosRequestConfig;
export const listEvaUserCredentials = () =>
  evaRequest.get(api.evaUserCredentials, evaCredentialConfig);

export const putEvaUserCredential = (
  connectorId: string,
  evaApiToken: string,
) =>
  evaRequest.put(
    api.evaUserCredential(connectorId),
    { eva_api_token: evaApiToken },
    evaCredentialConfig,
  );

export const deleteEvaUserCredential = (connectorId: string) =>
  evaRequest.delete(api.evaUserCredential(connectorId), evaCredentialConfig);

export default userService;
