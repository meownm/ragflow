import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import type {
  SqlCatalogBinding,
  SqlCatalogBindingInput,
  SqlExecutionConnector,
  SqlExecutionProfile,
  SqlExecutionProfileInput,
} from '@/pages/business-documents/types';
import {
  createBusinessDocumentSqlCatalogBinding,
  createBusinessDocumentSqlExecutionProfile,
  listBusinessDocumentSqlCatalogBindings,
  listBusinessDocumentSqlExecutionConnectors,
  listBusinessDocumentSqlExecutionProfiles,
  updateBusinessDocumentSqlCatalogBinding,
  updateBusinessDocumentSqlExecutionProfile,
} from '@/services/business-document-service';
import {
  AlertTriangle,
  Database,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCw,
} from 'lucide-react';
import { FormEvent, useState } from 'react';

const SELECT_CLASS =
  'h-8 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm text-text-primary outline-none focus:ring-1 focus:ring-accent-primary';

interface ProfileDraft {
  name: string;
  connectorId: string;
  allowedSchemas: string;
  statementTimeoutMs: string;
  maxRows: string;
  maxResultBytes: string;
  enabled: boolean;
}

interface BindingDraft {
  catalogService: string;
  catalogDatabase: string;
  catalogSchema: string;
  profileId: string;
  enabled: boolean;
}

const EMPTY_PROFILE: ProfileDraft = {
  name: '',
  connectorId: '',
  allowedSchemas: '',
  statementTimeoutMs: '30000',
  maxRows: '1000',
  maxResultBytes: '5000000',
  enabled: true,
};

const EMPTY_BINDING: BindingDraft = {
  catalogService: '',
  catalogDatabase: '',
  catalogSchema: '',
  profileId: '',
  enabled: true,
};

function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : 'Не удалось обновить реестр выполнения SQL.';
}

function schemas(value: string) {
  return [
    ...new Set(
      value
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

function profilePayload(
  profile: SqlExecutionProfile,
  enabled = profile.enabled,
) {
  return {
    schema_version: '1' as const,
    name: profile.name,
    connector_id: profile.connector.id,
    dialect: 'postgres' as const,
    allowed_schemas: profile.allowed_schemas,
    statement_timeout_ms: profile.statement_timeout_ms,
    max_rows: profile.max_rows,
    max_result_bytes: profile.max_result_bytes,
    enabled,
    expected_version: profile.version,
  };
}

function bindingPayload(binding: SqlCatalogBinding, enabled = binding.enabled) {
  return {
    schema_version: '1' as const,
    catalog_service: binding.catalog_service,
    catalog_database: binding.catalog_database,
    catalog_schema: binding.catalog_schema,
    execution_profile_id: binding.execution_profile_id,
    enabled,
    expected_version: binding.version,
  };
}

function disablePayload(version: number) {
  return {
    schema_version: '1' as const,
    enabled: false as const,
    expected_version: version,
  };
}

export function ExecutionRegistryDialog() {
  const [open, setOpen] = useState(false);
  const [connectors, setConnectors] = useState<SqlExecutionConnector[]>([]);
  const [profiles, setProfiles] = useState<SqlExecutionProfile[]>([]);
  const [bindings, setBindings] = useState<SqlCatalogBinding[]>([]);
  const [profileDraft, setProfileDraft] = useState<ProfileDraft>(EMPTY_PROFILE);
  const [bindingDraft, setBindingDraft] = useState<BindingDraft>(EMPTY_BINDING);
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const [editingBindingId, setEditingBindingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadRegistry = async () => {
    setLoading(true);
    setError(null);
    try {
      const [connectorResult, profileResult, bindingResult] = await Promise.all(
        [
          listBusinessDocumentSqlExecutionConnectors(),
          listBusinessDocumentSqlExecutionProfiles(),
          listBusinessDocumentSqlCatalogBindings(),
        ],
      );
      setConnectors(connectorResult.items);
      setProfiles(profileResult.items);
      setBindings(bindingResult.items);
      setProfileDraft((current) => ({
        ...current,
        connectorId:
          current.connectorId ||
          connectorResult.items.find((item) => item.available)?.id ||
          '',
      }));
      setBindingDraft((current) => ({
        ...current,
        profileId:
          current.profileId ||
          profileResult.items.find((item) => item.available)?.id ||
          '',
      }));
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setLoading(false);
    }
  };

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen) void loadRegistry();
  };

  const saveProfile = async (event: FormEvent) => {
    event.preventDefault();
    const allowedSchemas = schemas(profileDraft.allowedSchemas);
    if (
      !profileDraft.name.trim() ||
      !profileDraft.connectorId ||
      !allowedSchemas.length
    ) {
      setError('Укажите название, PostgreSQL-коннектор и хотя бы одну схему.');
      return;
    }
    const input: SqlExecutionProfileInput = {
      schema_version: '1',
      name: profileDraft.name.trim(),
      connector_id: profileDraft.connectorId,
      dialect: 'postgres',
      allowed_schemas: allowedSchemas,
      statement_timeout_ms: Number(profileDraft.statementTimeoutMs),
      max_rows: Number(profileDraft.maxRows),
      max_result_bytes: Number(profileDraft.maxResultBytes),
      enabled: profileDraft.enabled,
    };
    setSaving(true);
    setError(null);
    try {
      if (editingProfileId) {
        const current = profiles.find(
          (profile) => profile.id === editingProfileId,
        );
        if (!current) throw new Error('Профиль изменился. Обновите реестр.');
        await updateBusinessDocumentSqlExecutionProfile(editingProfileId, {
          ...input,
          expected_version: current.version,
        });
        setNotice('Профиль выполнения обновлён.');
      } else {
        await createBusinessDocumentSqlExecutionProfile(input);
        setNotice('Профиль выполнения создан.');
      }
      setEditingProfileId(null);
      setProfileDraft({
        ...EMPTY_PROFILE,
        connectorId: connectors.find((item) => item.available)?.id || '',
      });
      await loadRegistry();
    } catch (saveError) {
      setError(errorMessage(saveError));
    } finally {
      setSaving(false);
    }
  };

  const saveBinding = async (event: FormEvent) => {
    event.preventDefault();
    if (
      !bindingDraft.catalogService.trim() ||
      !bindingDraft.catalogDatabase.trim() ||
      !bindingDraft.catalogSchema.trim() ||
      !bindingDraft.profileId
    ) {
      setError('Заполните service, database, schema и выберите профиль.');
      return;
    }
    const input: SqlCatalogBindingInput = {
      schema_version: '1',
      catalog_service: bindingDraft.catalogService.trim(),
      catalog_database: bindingDraft.catalogDatabase.trim(),
      catalog_schema: bindingDraft.catalogSchema.trim(),
      execution_profile_id: bindingDraft.profileId,
      enabled: bindingDraft.enabled,
    };
    setSaving(true);
    setError(null);
    try {
      if (editingBindingId) {
        const current = bindings.find(
          (binding) => binding.id === editingBindingId,
        );
        if (!current) throw new Error('Связь изменилась. Обновите реестр.');
        await updateBusinessDocumentSqlCatalogBinding(editingBindingId, {
          ...input,
          expected_version: current.version,
        });
        setNotice('Связь каталога обновлена.');
      } else {
        await createBusinessDocumentSqlCatalogBinding(input);
        setNotice('Связь каталога создана.');
      }
      setEditingBindingId(null);
      setBindingDraft({
        ...EMPTY_BINDING,
        profileId: profiles.find((item) => item.available)?.id || '',
      });
      await loadRegistry();
    } catch (saveError) {
      setError(errorMessage(saveError));
    } finally {
      setSaving(false);
    }
  };

  const editProfile = (profile: SqlExecutionProfile) => {
    setEditingProfileId(profile.id);
    setProfileDraft({
      name: profile.name,
      connectorId: profile.connector.id,
      allowedSchemas: profile.allowed_schemas.join(', '),
      statementTimeoutMs: String(profile.statement_timeout_ms),
      maxRows: String(profile.max_rows),
      maxResultBytes: String(profile.max_result_bytes),
      enabled: profile.enabled,
    });
  };

  const editBinding = (binding: SqlCatalogBinding) => {
    setEditingBindingId(binding.id);
    setBindingDraft({
      catalogService: binding.catalog_service,
      catalogDatabase: binding.catalog_database,
      catalogSchema: binding.catalog_schema,
      profileId: binding.execution_profile_id,
      enabled: binding.enabled,
    });
  };

  const toggleProfile = async (profile: SqlExecutionProfile) => {
    setSaving(true);
    setError(null);
    try {
      await updateBusinessDocumentSqlExecutionProfile(
        profile.id,
        profile.enabled
          ? disablePayload(profile.version)
          : profilePayload(profile, true),
      );
      setNotice(profile.enabled ? 'Профиль отключён.' : 'Профиль включён.');
      await loadRegistry();
    } catch (toggleError) {
      setError(errorMessage(toggleError));
    } finally {
      setSaving(false);
    }
  };

  const toggleBinding = async (binding: SqlCatalogBinding) => {
    setSaving(true);
    setError(null);
    try {
      await updateBusinessDocumentSqlCatalogBinding(
        binding.id,
        binding.enabled
          ? disablePayload(binding.version)
          : bindingPayload(binding, true),
      );
      setNotice(binding.enabled ? 'Связь отключена.' : 'Связь включена.');
      await loadRegistry();
    } catch (toggleError) {
      setError(errorMessage(toggleError));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          data-testid="open-sql-execution-registry"
        >
          <Database className="size-4" />
          <span className="hidden sm:inline">Источники SQL</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[94vh] max-w-[min(1180px,calc(100vw-2rem))] overflow-hidden">
        <DialogHeader>
          <DialogTitle>Центральный реестр выполнения SQL</DialogTitle>
          <DialogDescription>
            Профиль задаёт PostgreSQL-коннектор и жёсткие лимиты. Связь
            сопоставляет точный service/database/schema из OpenMetadata с
            профилем. Секреты и сохранённый ingestion query здесь не
            отображаются.
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 space-y-5 overflow-y-auto pe-2">
          <div className="flex min-h-8 items-center justify-between gap-3">
            <div className="text-xs">
              {loading && (
                <span className="flex items-center gap-2 text-text-secondary">
                  <LoaderCircle className="size-4 animate-spin" />
                  Загружаем реестр…
                </span>
              )}
              {error && (
                <span
                  className="flex items-start gap-2 text-state-error"
                  role="alert"
                >
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  {error}
                </span>
              )}
              {!error && notice && (
                <span className="text-state-success" role="status">
                  {notice}
                </span>
              )}
            </div>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => void loadRegistry()}
              disabled={loading || saving}
            >
              <RefreshCw className="size-4" />
              Обновить
            </Button>
          </div>

          <section className="grid gap-4 border-t border-border-button pt-4 lg:grid-cols-[minmax(320px,0.9fr)_minmax(420px,1.1fr)]">
            <form className="space-y-3" onSubmit={saveProfile}>
              <div>
                <h3 className="text-sm font-semibold">
                  {editingProfileId ? 'Редактировать профиль' : 'Новый профиль'}
                </h3>
                <p className="mt-1 text-xs text-text-secondary">
                  Первый execution adapter — только PostgreSQL.
                </p>
              </div>
              <label className="block space-y-1 text-xs font-medium">
                <span>Название</span>
                <Input
                  aria-label="Название execution profile"
                  value={profileDraft.name}
                  maxLength={128}
                  onChange={(event) =>
                    setProfileDraft((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="block space-y-1 text-xs font-medium">
                <span>PostgreSQL-коннектор</span>
                <select
                  className={SELECT_CLASS}
                  aria-label="PostgreSQL-коннектор execution profile"
                  value={profileDraft.connectorId}
                  onChange={(event) =>
                    setProfileDraft((current) => ({
                      ...current,
                      connectorId: event.target.value,
                    }))
                  }
                >
                  <option value="">Выберите коннектор</option>
                  {connectors.map((connector) => (
                    <option
                      key={connector.id}
                      value={connector.id}
                      disabled={!connector.available}
                    >
                      {connector.name} · {connector.database || 'неполный'}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block space-y-1 text-xs font-medium">
                <span>Разрешённые схемы через запятую</span>
                <Input
                  aria-label="Разрешённые схемы execution profile"
                  value={profileDraft.allowedSchemas}
                  placeholder="dwh, ref"
                  onChange={(event) =>
                    setProfileDraft((current) => ({
                      ...current,
                      allowedSchemas: event.target.value,
                    }))
                  }
                />
              </label>
              <div className="grid gap-2 sm:grid-cols-3">
                <label className="space-y-1 text-xs font-medium">
                  <span>Timeout, мс</span>
                  <Input
                    type="number"
                    min={100}
                    max={120000}
                    aria-label="Statement timeout execution profile"
                    value={profileDraft.statementTimeoutMs}
                    onChange={(event) =>
                      setProfileDraft((current) => ({
                        ...current,
                        statementTimeoutMs: event.target.value,
                      }))
                    }
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  <span>Строк</span>
                  <Input
                    type="number"
                    min={1}
                    max={10000}
                    aria-label="Максимум строк execution profile"
                    value={profileDraft.maxRows}
                    onChange={(event) =>
                      setProfileDraft((current) => ({
                        ...current,
                        maxRows: event.target.value,
                      }))
                    }
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  <span>Байт</span>
                  <Input
                    type="number"
                    min={1024}
                    max={50000000}
                    aria-label="Максимум байт execution profile"
                    value={profileDraft.maxResultBytes}
                    onChange={(event) =>
                      setProfileDraft((current) => ({
                        ...current,
                        maxResultBytes: event.target.value,
                      }))
                    }
                  />
                </label>
              </div>
              <label className="flex items-center gap-2 text-xs font-medium">
                <input
                  type="checkbox"
                  checked={profileDraft.enabled}
                  onChange={(event) =>
                    setProfileDraft((current) => ({
                      ...current,
                      enabled: event.target.checked,
                    }))
                  }
                />
                Профиль активен
              </label>
              <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={saving || loading}>
                  {saving ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : editingProfileId ? (
                    <Pencil className="size-4" />
                  ) : (
                    <Plus className="size-4" />
                  )}
                  {editingProfileId ? 'Сохранить профиль' : 'Создать профиль'}
                </Button>
                {editingProfileId && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditingProfileId(null);
                      setProfileDraft({
                        ...EMPTY_PROFILE,
                        connectorId:
                          connectors.find((item) => item.available)?.id || '',
                      });
                    }}
                  >
                    Отмена
                  </Button>
                )}
              </div>
            </form>

            <div className="space-y-2" data-testid="sql-execution-profile-list">
              <h3 className="text-sm font-semibold">
                Профили · {profiles.length}
              </h3>
              {profiles.length ? (
                profiles.map((profile) => (
                  <article
                    key={profile.id}
                    className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border-button p-3 text-xs"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-medium text-text-primary">
                          {profile.name}
                        </p>
                        <Badge
                          variant="outline"
                          className={
                            profile.available
                              ? 'border-state-success/40 text-state-success'
                              : 'border-state-warning/40 text-state-warning'
                          }
                        >
                          {profile.available
                            ? 'ACTIVE'
                            : profile.enabled
                              ? 'INVALID'
                              : 'DISABLED'}
                        </Badge>
                      </div>
                      <p className="mt-1 text-text-secondary">
                        {profile.connector.name} · {profile.target_database} ·{' '}
                        {profile.allowed_schemas.join(', ')}
                      </p>
                      <p className="mt-1 text-text-secondary">
                        timeout {profile.statement_timeout_ms} мс · rows{' '}
                        {profile.max_rows} · bytes {profile.max_result_bytes} ·
                        v{profile.version}
                      </p>
                      {profile.enabled && !profile.policy_valid && (
                        <p className="mt-1 text-state-error">
                          Сохранённая политика не прошла проверку контракта.
                        </p>
                      )}
                      {profile.enabled && !profile.connector_available && (
                        <p className="mt-1 text-state-error">
                          Коннектор удалён, имеет другой тип или заполнен не
                          полностью.
                        </p>
                      )}
                      {profile.enabled &&
                        profile.connector_available &&
                        !profile.connector_identity_matches && (
                          <p className="mt-1 text-state-warning">
                            Target или роль коннектора изменились. Проверьте и
                            явно сохраните новую версию профиля.
                          </p>
                        )}
                    </div>
                    <div className="flex gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => editProfile(profile)}
                      >
                        Изменить
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => void toggleProfile(profile)}
                        disabled={saving}
                      >
                        {profile.enabled ? 'Отключить' : 'Включить'}
                      </Button>
                    </div>
                  </article>
                ))
              ) : (
                <p className="rounded-md border border-dashed border-border-button p-4 text-xs text-text-secondary">
                  Профилей пока нет.
                </p>
              )}
            </div>
          </section>

          <section className="grid gap-4 border-t border-border-button pt-4 lg:grid-cols-[minmax(320px,0.9fr)_minmax(420px,1.1fr)]">
            <form className="space-y-3" onSubmit={saveBinding}>
              <div>
                <h3 className="text-sm font-semibold">
                  {editingBindingId ? 'Редактировать связь' : 'Новая связь'}
                </h3>
                <p className="mt-1 text-xs text-text-secondary">
                  Идентификаторы должны точно совпасть со снимком OpenMetadata.
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-3">
                <label className="space-y-1 text-xs font-medium">
                  <span>Service</span>
                  <Input
                    aria-label="Catalog service"
                    value={bindingDraft.catalogService}
                    onChange={(event) =>
                      setBindingDraft((current) => ({
                        ...current,
                        catalogService: event.target.value,
                      }))
                    }
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  <span>Database</span>
                  <Input
                    aria-label="Catalog database"
                    value={bindingDraft.catalogDatabase}
                    onChange={(event) =>
                      setBindingDraft((current) => ({
                        ...current,
                        catalogDatabase: event.target.value,
                      }))
                    }
                  />
                </label>
                <label className="space-y-1 text-xs font-medium">
                  <span>Schema</span>
                  <Input
                    aria-label="Catalog schema"
                    value={bindingDraft.catalogSchema}
                    onChange={(event) =>
                      setBindingDraft((current) => ({
                        ...current,
                        catalogSchema: event.target.value,
                      }))
                    }
                  />
                </label>
              </div>
              <label className="block space-y-1 text-xs font-medium">
                <span>Execution profile</span>
                <select
                  className={SELECT_CLASS}
                  aria-label="Execution profile для связи каталога"
                  value={bindingDraft.profileId}
                  onChange={(event) =>
                    setBindingDraft((current) => ({
                      ...current,
                      profileId: event.target.value,
                    }))
                  }
                >
                  <option value="">Выберите профиль</option>
                  {profiles.map((profile) => (
                    <option
                      key={profile.id}
                      value={profile.id}
                      disabled={
                        !profile.available &&
                        profile.id !== bindingDraft.profileId
                      }
                    >
                      {profile.name} · {profile.target_database || 'нет БД'}
                      {profile.available ? '' : ' · недоступен'}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs font-medium">
                <input
                  type="checkbox"
                  checked={bindingDraft.enabled}
                  onChange={(event) =>
                    setBindingDraft((current) => ({
                      ...current,
                      enabled: event.target.checked,
                    }))
                  }
                />
                Связь активна
              </label>
              <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={saving || loading}>
                  {saving ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : editingBindingId ? (
                    <Pencil className="size-4" />
                  ) : (
                    <Plus className="size-4" />
                  )}
                  {editingBindingId ? 'Сохранить связь' : 'Создать связь'}
                </Button>
                {editingBindingId && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditingBindingId(null);
                      setBindingDraft({
                        ...EMPTY_BINDING,
                        profileId:
                          profiles.find((item) => item.available)?.id || '',
                      });
                    }}
                  >
                    Отмена
                  </Button>
                )}
              </div>
            </form>

            <div className="space-y-2" data-testid="sql-catalog-binding-list">
              <h3 className="text-sm font-semibold">
                Связи каталога · {bindings.length}
              </h3>
              {bindings.length ? (
                bindings.map((binding) => (
                  <article
                    key={binding.id}
                    className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border-button p-3 text-xs"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="break-all font-medium text-text-primary">
                          {binding.catalog_service}/{binding.catalog_database}/
                          {binding.catalog_schema}
                        </p>
                        <Badge variant="outline">
                          {binding.enabled ? 'ACTIVE' : 'DISABLED'}
                        </Badge>
                      </div>
                      <p className="mt-1 text-text-secondary">
                        Профиль:{' '}
                        {profiles.find(
                          (profile) =>
                            profile.id === binding.execution_profile_id,
                        )?.name || binding.execution_profile_id}{' '}
                        · v{binding.version}
                      </p>
                    </div>
                    <div className="flex gap-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => editBinding(binding)}
                      >
                        Изменить
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => void toggleBinding(binding)}
                        disabled={saving}
                      >
                        {binding.enabled ? 'Отключить' : 'Включить'}
                      </Button>
                    </div>
                  </article>
                ))
              ) : (
                <p className="rounded-md border border-dashed border-border-button p-4 text-xs text-text-secondary">
                  Связей пока нет.
                </p>
              )}
            </div>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
