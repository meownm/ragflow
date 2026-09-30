import PasswordInput from '@/components/originui/password-input';
import { SelectWithSearch } from '@/components/originui/select-with-search';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import message from '@/components/ui/message';
import {
  discoverBusinessDocumentsEvaSpaces,
  getBusinessDocumentsCatalogStatus,
  getBusinessDocumentsSettings,
  importBusinessDocumentsCatalog,
  setBusinessDocumentsSettings,
} from '@/services/admin-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

const queryKey = ['admin', 'business-documents-settings'];
const catalogQueryKey = ['admin', 'business-documents-catalog'];
const emptyConnection = {
  api_base_url: '',
  web_base_url: '',
  project_id: '',
  verify_ssl: true,
  include_archived: false,
};

export default function AdminBusinessDocumentsSettings() {
  const { t } = useTranslation();
  const label = (key: string) =>
    t(`admin.businessDocumentsSettingsPage.${key}`);
  const queryClient = useQueryClient();
  const [connection, setConnection] = useState(emptyConnection);
  const [token, setToken] = useState('');
  const [clearToken, setClearToken] = useState(false);
  const [spaces, setSpaces] = useState<{ id: string; name: string }[]>([]);
  const [catalogFile, setCatalogFile] = useState<File | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey,
    queryFn: async () => {
      const { data: response } = await getBusinessDocumentsSettings();
      if (response.code !== 0) throw new Error(response.message);
      return response.data;
    },
  });
  useEffect(() => {
    if (data) {
      setConnection({
        api_base_url: data.eva_connection.api_base_url,
        web_base_url: data.eva_connection.web_base_url,
        project_id: data.eva_connection.project_id,
        verify_ssl: data.eva_connection.verify_ssl,
        include_archived: data.eva_connection.include_archived,
      });
      setToken('');
      setClearToken(false);
    }
  }, [data]);
  const payload = {
    ...connection,
    eva_api_token: token,
    clear_token: clearToken,
  };
  const discovery = useMutation({
    mutationFn: async () => {
      const { data: response } =
        await discoverBusinessDocumentsEvaSpaces(payload);
      if (response.code !== 0) throw new Error(response.message);
      return response.data.items;
    },
    onSuccess: setSpaces,
  });
  const mutation = useMutation({
    mutationFn: async (disable: boolean) => {
      const { data: response } = await setBusinessDocumentsSettings(
        disable ? null : payload,
      );
      if (response.code !== 0) throw new Error(response.message);
      return response.data;
    },
    onSuccess: async (saved) => {
      queryClient.setQueryData(queryKey, saved);
      setToken('');
      setClearToken(false);
      setSpaces([]);
      discovery.reset();
      await queryClient.invalidateQueries({
        queryKey: ['eva-user-credentials'],
      });
      message.success(label('saved'));
    },
  });
  const catalog = useQuery({
    queryKey: catalogQueryKey,
    queryFn: async () => {
      const { data: response } = await getBusinessDocumentsCatalogStatus();
      if (response.code !== 0) throw new Error(response.message);
      return response.data;
    },
  });
  const catalogImport = useMutation({
    mutationFn: async (file: File) => {
      const { data: response } = await importBusinessDocumentsCatalog(file);
      if (response.code !== 0) throw new Error(response.message);
      return response.data;
    },
    onSuccess: (status) => {
      queryClient.setQueryData(catalogQueryKey, status);
      message.success(
        t('admin.businessDocumentsSettingsPage.catalogImported', {
          count: status.active_items,
        }),
      );
    },
  });
  const busy = isLoading || mutation.isPending || discovery.isPending;
  const update = (
    key: keyof typeof emptyConnection,
    value: string | boolean,
  ) => {
    setConnection((current) => ({ ...current, [key]: value }));
    setSpaces([]);
    discovery.reset();
  };
  return (
    <Card
      className="h-full overflow-y-auto rounded-xl border-0.5 border-border-button bg-transparent !shadow-none"
      data-testid="business-documents-settings-admin"
    >
      <CardHeader className="border-b border-border-button">
        <CardTitle>{label('catalogTitle')}</CardTitle>
        <CardDescription>{label('catalogDescription')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5 pt-6">
        {catalog.data && (
          <dl className="grid max-w-3xl grid-cols-1 gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-text-secondary">{label('catalogVersion')}</dt>
              <dd className="font-medium">
                {catalog.data.source_id} · {catalog.data.source_version}
              </dd>
            </div>
            <div>
              <dt className="text-text-secondary">{label('catalogRecords')}</dt>
              <dd className="font-medium">{catalog.data.active_items}</dd>
            </div>
            <div>
              <dt className="text-text-secondary">{label('catalogFile')}</dt>
              <dd className="font-medium">{catalog.data.filename}</dd>
            </div>
            <div>
              <dt className="text-text-secondary">{label('catalogHash')}</dt>
              <dd className="break-all font-mono text-xs">
                {catalog.data.source_sha256}
              </dd>
            </div>
          </dl>
        )}
        <div className="max-w-3xl space-y-2">
          <label
            className="text-sm font-medium"
            htmlFor="business-documents-catalog-file"
          >
            {label('catalogSelect')}
          </label>
          <Input
            id="business-documents-catalog-file"
            type="file"
            accept=".json,application/json"
            disabled={catalogImport.isPending}
            onChange={(event) => {
              setCatalogFile(event.target.files?.[0] ?? null);
              catalogImport.reset();
            }}
          />
          <p className="text-sm text-text-secondary">{label('catalogHelp')}</p>
        </div>
        {(catalogImport.error || catalog.error) && (
          <p className="text-sm text-state-error" role="alert">
            {(catalogImport.error || catalog.error)?.message}
          </p>
        )}
        <div className="flex justify-end">
          <Button
            data-testid="business-documents-catalog-import"
            disabled={!catalogFile || catalogImport.isPending}
            onClick={() => catalogFile && catalogImport.mutate(catalogFile)}
          >
            {label(
              catalogImport.isPending ? 'catalogImporting' : 'catalogImport',
            )}
          </Button>
        </div>
      </CardContent>
      <CardHeader className="border-y border-border-button">
        <CardTitle>{t('admin.businessDocumentsSettings')}</CardTitle>
        <CardDescription>{label('description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6 pt-6">
        <fieldset disabled={busy || !!error} className="max-w-2xl space-y-5">
          {(['api_base_url', 'web_base_url'] as const).map((key) => (
            <div key={key} className="space-y-2">
              <label className="text-sm font-medium" htmlFor={key}>
                {label(key)}
              </label>
              <Input
                id={key}
                value={connection[key]}
                onChange={(event) => update(key, event.target.value)}
                placeholder="https://eva.example.com"
                maxLength={2048}
              />
            </div>
          ))}
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor="eva-token">
              {label('token')}
            </label>
            <PasswordInput
              id="eva-token"
              value={token}
              onChange={(event) => {
                setToken(event.target.value);
                setClearToken(false);
                setSpaces([]);
                discovery.reset();
              }}
              maxLength={4096}
              autoComplete="new-password"
              placeholder={label(
                data?.eva_connection.token_configured
                  ? 'tokenSaved'
                  : 'tokenOptional',
              )}
            />
            <p className="text-sm text-text-secondary">{label('tokenHelp')}</p>
            {data?.eva_connection.token_configured && (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={clearToken}
                  onChange={(event) => {
                    setClearToken(event.target.checked);
                    setToken('');
                    setSpaces([]);
                    discovery.reset();
                  }}
                />
                {label('clearToken')}
              </label>
            )}
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor="project_id">
              {label('evaSpace')}
            </label>
            <div className="flex gap-2">
              <Input
                id="project_id"
                value={connection.project_id}
                onChange={(event) =>
                  setConnection((current) => ({
                    ...current,
                    project_id: event.target.value,
                  }))
                }
                placeholder="CmfProject:..."
                maxLength={2048}
              />
              <Button
                variant="outline"
                onClick={() => discovery.mutate()}
                disabled={busy || !connection.api_base_url.trim()}
              >
                {label(discovery.isPending ? 'loadingSpaces' : 'loadSpaces')}
              </Button>
            </div>
            {spaces.length > 0 && (
              <SelectWithSearch
                value={connection.project_id}
                onChange={(value) =>
                  setConnection((current) => ({
                    ...current,
                    project_id: value,
                  }))
                }
                options={spaces.map((space) => ({
                  value: space.id,
                  label: space.name,
                }))}
                testId="business-documents-eva-space-select"
              />
            )}
            {discovery.isSuccess && spaces.length === 0 && (
              <p className="text-sm text-text-secondary">
                {label('evaSpacesEmpty')}
              </p>
            )}
            <p className="text-sm text-text-secondary">
              {label('evaSpaceHelp')}
            </p>
          </div>
          {(['verify_ssl', 'include_archived'] as const).map((key) => (
            <label key={key} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={connection[key]}
                onChange={(event) => update(key, event.target.checked)}
              />
              {label(key)}
            </label>
          ))}
        </fieldset>
        {(error || mutation.error || discovery.error) && (
          <p className="text-sm text-state-error" role="alert">
            {(error || mutation.error || discovery.error)?.message}
          </p>
        )}
        <div className="flex justify-end gap-3">
          <Button
            variant="outline"
            onClick={() => mutation.mutate(true)}
            disabled={busy || !!error || !data?.eva_connection.api_base_url}
          >
            {label('disconnect')}
          </Button>
          <Button
            data-testid="business-documents-settings-save"
            onClick={() => mutation.mutate(false)}
            disabled={
              busy ||
              !!error ||
              !connection.api_base_url.trim() ||
              !connection.project_id.trim()
            }
          >
            {label(mutation.isPending ? 'saving' : 'save')}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
