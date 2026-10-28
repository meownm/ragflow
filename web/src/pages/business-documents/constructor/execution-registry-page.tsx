import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import type {
  SqlCatalogBinding,
  SqlCatalogBindingInput,
  SqlExecutionConnector,
  SqlExecutionProfile,
  SqlExecutionProfileInput,
} from '@/pages/business-documents/types';
import { Routes } from '@/routes';
import {
  createBusinessDocumentSqlCatalogBinding,
  createBusinessDocumentSqlExecutionProfile,
  getBusinessDocumentCapabilities,
  listBusinessDocumentSqlCatalogBindings,
  listBusinessDocumentSqlExecutionConnectors,
  listBusinessDocumentSqlExecutionProfiles,
  updateBusinessDocumentSqlCatalogBinding,
  updateBusinessDocumentSqlExecutionProfile,
} from '@/services/business-document-service';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCw,
} from 'lucide-react';
import { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';

const SELECT_CLASS =
  'h-8 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm text-text-primary outline-none focus:ring-1 focus:ring-accent-primary';

interface ProfileDraft {
  name: string;
  connectorId: string;
  allowedSchemas: string;
  statementTimeoutSeconds: string;
  maxRows: string;
  maxResultMb: string;
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
  statementTimeoutSeconds: '30',
  maxRows: '1000',
  maxResultMb: '5',
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

function ExecutionRegistryWorkspace() {
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
  const [section, setSection] = useState<'profiles' | 'bindings'>('profiles');
  const [formOpen, setFormOpen] = useState(false);
  const [pendingDisable, setPendingDisable] = useState<
    | { kind: 'profile'; item: SqlExecutionProfile }
    | { kind: 'binding'; item: SqlCatalogBinding }
    | null
  >(null);
  const formRef = useRef<HTMLDivElement>(null);

  const loadRegistry = useCallback(async () => {
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
  }, []);

  useEffect(() => {
    void loadRegistry();
  }, [loadRegistry]);

  useEffect(() => {
    if (formOpen) formRef.current?.scrollIntoView?.({ block: 'start' });
  }, [editingBindingId, editingProfileId, formOpen, section]);

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
      statement_timeout_ms: Number(profileDraft.statementTimeoutSeconds) * 1000,
      max_rows: Number(profileDraft.maxRows),
      max_result_bytes: Number(profileDraft.maxResultMb) * 1000000,
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
      setFormOpen(false);
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
      setError('Укажите сервис, базу данных, схему и профиль выполнения.');
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
        setNotice('Привязка схемы обновлена.');
      } else {
        await createBusinessDocumentSqlCatalogBinding(input);
        setNotice('Схема привязана к профилю.');
      }
      setEditingBindingId(null);
      setFormOpen(false);
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
    setSection('profiles');
    setFormOpen(true);
    setNotice(null);
    setEditingProfileId(profile.id);
    setProfileDraft({
      name: profile.name,
      connectorId: profile.connector.id,
      allowedSchemas: profile.allowed_schemas.join(', '),
      statementTimeoutSeconds: String(profile.statement_timeout_ms / 1000),
      maxRows: String(profile.max_rows),
      maxResultMb: String(profile.max_result_bytes / 1000000),
      enabled: profile.enabled,
    });
  };

  const editBinding = (binding: SqlCatalogBinding) => {
    setSection('bindings');
    setFormOpen(true);
    setNotice(null);
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
      setNotice(binding.enabled ? 'Привязка отключена.' : 'Привязка включена.');
      await loadRegistry();
    } catch (toggleError) {
      setError(errorMessage(toggleError));
    } finally {
      setSaving(false);
    }
  };

  const activeBindings =
    pendingDisable?.kind === 'profile'
      ? bindings.filter(
          (binding) =>
            binding.enabled &&
            binding.execution_profile_id === pendingDisable.item.id,
        )
      : [];

  return (
    <main
      className="h-full min-h-0 overflow-y-auto bg-bg-base text-text-primary"
      data-testid="sql-execution-registry-page"
    >
      <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
        <Link
          to={Routes.SqlProjects}
          className="mb-5 inline-flex items-center gap-2 text-sm text-text-secondary hover:text-text-primary"
        >
          <ArrowLeft className="size-4" /> Вернуться к SQL-проектам
        </Link>
        <header className="flex flex-wrap items-end justify-between gap-4 border-b border-border-button pb-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              Источники SQL
            </h1>
            <p className="mt-2 max-w-2xl text-sm text-text-secondary">
              Настройте подключение и ограничения запросов, затем привяжите
              схемы каталога к профилю.
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void loadRegistry()}
            disabled={loading || saving}
          >
            <RefreshCw className="size-4" /> Обновить
          </Button>
        </header>
        <div className="mt-5 min-h-6 text-sm">
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
        <nav
          aria-label="Разделы источников SQL"
          className="mt-4 flex gap-6 border-b border-border-button"
        >
          <button
            type="button"
            aria-current={section === 'profiles' ? 'page' : undefined}
            className={`border-b-2 pb-3 text-sm font-medium ${section === 'profiles' ? 'border-accent-primary text-text-primary' : 'border-transparent text-text-secondary'}`}
            onClick={() => {
              setSection('profiles');
              setFormOpen(false);
            }}
          >
            Профили выполнения{' '}
            <span className="ms-1 text-text-secondary">{profiles.length}</span>
          </button>
          <button
            type="button"
            aria-current={section === 'bindings' ? 'page' : undefined}
            className={`border-b-2 pb-3 text-sm font-medium ${section === 'bindings' ? 'border-accent-primary text-text-primary' : 'border-transparent text-text-secondary'}`}
            onClick={() => {
              setSection('bindings');
              setFormOpen(false);
            }}
          >
            Привязки схем{' '}
            <span className="ms-1 text-text-secondary">{bindings.length}</span>
          </button>
        </nav>

        {section === 'profiles' && (
          <section className="py-6">
            <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">Профили выполнения</h2>
                <p className="mt-1 text-sm text-text-secondary">
                  Подключение к PostgreSQL и пределы выполнения запросов.
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                onClick={() => {
                  setEditingProfileId(null);
                  setProfileDraft({
                    ...EMPTY_PROFILE,
                    connectorId:
                      connectors.find((item) => item.available)?.id || '',
                  });
                  setFormOpen(true);
                }}
              >
                <Plus className="size-4" /> Добавить профиль
              </Button>
            </div>
            {formOpen && (
              <div
                ref={formRef}
                className="mb-6 max-w-2xl scroll-mt-6 rounded-lg border border-border-button bg-bg-input p-5"
              >
                <form className="space-y-3" onSubmit={saveProfile}>
                  <div>
                    <h3 className="text-sm font-semibold">
                      {editingProfileId
                        ? 'Редактировать профиль'
                        : 'Новый профиль'}
                    </h3>
                    <p className="mt-1 text-xs text-text-secondary">
                      Здесь доступны подключения PostgreSQL.
                    </p>
                  </div>
                  <label className="block space-y-1 text-xs font-medium">
                    <span>Название</span>
                    <Input
                      value={profileDraft.name}
                      required
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
                      value={profileDraft.connectorId}
                      required
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
                    <span>Разрешённые схемы PostgreSQL</span>
                    <Input
                      value={profileDraft.allowedSchemas}
                      required
                      placeholder="dwh, ref"
                      onChange={(event) =>
                        setProfileDraft((current) => ({
                          ...current,
                          allowedSchemas: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <p className="text-xs text-text-secondary">
                    Введите точные имена схем через запятую, например dwh, ref.
                  </p>
                  <div className="grid gap-2 sm:grid-cols-3">
                    <label className="space-y-1 text-xs font-medium">
                      <span>Время выполнения, с</span>
                      <Input
                        type="number"
                        min={0.1}
                        max={120}
                        step={0.1}
                        value={profileDraft.statementTimeoutSeconds}
                        required
                        onChange={(event) =>
                          setProfileDraft((current) => ({
                            ...current,
                            statementTimeoutSeconds: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <label className="space-y-1 text-xs font-medium">
                      <span>Максимум строк</span>
                      <Input
                        type="number"
                        min={1}
                        max={10000}
                        value={profileDraft.maxRows}
                        required
                        onChange={(event) =>
                          setProfileDraft((current) => ({
                            ...current,
                            maxRows: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <label className="space-y-1 text-xs font-medium">
                      <span>Размер результата, МБ</span>
                      <Input
                        type="number"
                        min={0.001024}
                        max={50}
                        step={0.000001}
                        value={profileDraft.maxResultMb}
                        required
                        onChange={(event) =>
                          setProfileDraft((current) => ({
                            ...current,
                            maxResultMb: event.target.value,
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
                    <Button
                      type="submit"
                      size="sm"
                      disabled={saving || loading}
                    >
                      {saving ? (
                        <LoaderCircle className="size-4 animate-spin" />
                      ) : editingProfileId ? (
                        <Pencil className="size-4" />
                      ) : (
                        <Plus className="size-4" />
                      )}
                      {editingProfileId
                        ? 'Сохранить профиль'
                        : 'Создать профиль'}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={saving}
                      onClick={() => {
                        setEditingProfileId(null);
                        setFormOpen(false);
                        setError(null);
                        setProfileDraft(EMPTY_PROFILE);
                      }}
                    >
                      Отмена
                    </Button>
                  </div>
                </form>
              </div>
            )}

            <div
              className="divide-y divide-border-button border-y border-border-button"
              data-testid="sql-execution-profile-list"
            >
              {profiles.length ? (
                profiles.map((profile) => (
                  <article
                    key={profile.id}
                    className="flex flex-wrap items-start justify-between gap-3 py-4 text-sm"
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
                            ? 'Доступен'
                            : profile.enabled
                              ? 'Требует внимания'
                              : 'Отключён'}
                        </Badge>
                      </div>
                      <p className="mt-1 text-text-secondary">
                        {profile.connector.name} · {profile.target_database} ·{' '}
                        {profile.allowed_schemas.join(', ')}
                      </p>
                      <p className="mt-1 text-text-secondary">
                        Время: {profile.statement_timeout_ms / 1000} с · До{' '}
                        {profile.max_rows.toLocaleString('ru-RU')} строк · До{' '}
                        {profile.max_result_bytes / 1000000} МБ
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
                            База данных или роль подключения изменились.
                            Проверьте профиль и сохраните его заново.
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
                        onClick={() =>
                          profile.enabled
                            ? setPendingDisable({
                                kind: 'profile',
                                item: profile,
                              })
                            : void toggleProfile(profile)
                        }
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
        )}

        {section === 'bindings' && (
          <section className="py-6">
            <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">Привязки схем</h2>
                <p className="mt-1 text-sm text-text-secondary">
                  Укажите, какой профиль используется для каждой схемы
                  OpenMetadata.
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                onClick={() => {
                  setEditingBindingId(null);
                  setBindingDraft({
                    ...EMPTY_BINDING,
                    profileId:
                      profiles.find((item) => item.available)?.id || '',
                  });
                  setFormOpen(true);
                }}
              >
                <Plus className="size-4" /> Добавить привязку
              </Button>
            </div>
            {formOpen && (
              <div
                ref={formRef}
                className="mb-6 max-w-2xl scroll-mt-6 rounded-lg border border-border-button bg-bg-input p-5"
              >
                <form className="space-y-3" onSubmit={saveBinding}>
                  <div>
                    <h3 className="text-sm font-semibold">
                      {editingBindingId
                        ? 'Изменить привязку'
                        : 'Привязать схему'}
                    </h3>
                    <p className="mt-1 text-xs text-text-secondary">
                      Используйте точные имена сервиса, базы и схемы из
                      OpenMetadata.
                    </p>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-3">
                    <label className="space-y-1 text-xs font-medium">
                      <span>Сервис</span>
                      <Input
                        value={bindingDraft.catalogService}
                        required
                        onChange={(event) =>
                          setBindingDraft((current) => ({
                            ...current,
                            catalogService: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <label className="space-y-1 text-xs font-medium">
                      <span>База данных</span>
                      <Input
                        value={bindingDraft.catalogDatabase}
                        required
                        onChange={(event) =>
                          setBindingDraft((current) => ({
                            ...current,
                            catalogDatabase: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <label className="space-y-1 text-xs font-medium">
                      <span>Схема</span>
                      <Input
                        value={bindingDraft.catalogSchema}
                        required
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
                    <span>Профиль выполнения</span>
                    <select
                      className={SELECT_CLASS}
                      value={bindingDraft.profileId}
                      required
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
                    Привязка включена
                  </label>
                  <div className="flex gap-2">
                    <Button
                      type="submit"
                      size="sm"
                      disabled={saving || loading}
                    >
                      {saving ? (
                        <LoaderCircle className="size-4 animate-spin" />
                      ) : editingBindingId ? (
                        <Pencil className="size-4" />
                      ) : (
                        <Plus className="size-4" />
                      )}
                      {editingBindingId
                        ? 'Сохранить привязку'
                        : 'Привязать схему'}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={saving}
                      onClick={() => {
                        setEditingBindingId(null);
                        setFormOpen(false);
                        setError(null);
                        setBindingDraft(EMPTY_BINDING);
                      }}
                    >
                      Отмена
                    </Button>
                  </div>
                </form>
              </div>
            )}

            <div
              className="divide-y divide-border-button border-y border-border-button"
              data-testid="sql-catalog-binding-list"
            >
              {bindings.length ? (
                bindings.map((binding) => (
                  <article
                    key={binding.id}
                    className="flex flex-wrap items-start justify-between gap-3 py-4 text-sm"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="break-all font-medium text-text-primary">
                          {binding.catalog_service}/{binding.catalog_database}/
                          {binding.catalog_schema}
                        </p>
                        <Badge variant="outline">
                          {!binding.enabled
                            ? 'Отключена'
                            : profiles.find(
                                  (profile) =>
                                    profile.id === binding.execution_profile_id,
                                )?.available
                              ? 'Включена'
                              : 'Профиль недоступен'}
                        </Badge>
                      </div>
                      <p className="mt-1 text-text-secondary">
                        Профиль:{' '}
                        {profiles.find(
                          (profile) =>
                            profile.id === binding.execution_profile_id,
                        )?.name || binding.execution_profile_id}{' '}
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
                        onClick={() =>
                          binding.enabled
                            ? setPendingDisable({
                                kind: 'binding',
                                item: binding,
                              })
                            : void toggleBinding(binding)
                        }
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
        )}
      </div>
      <AlertDialog
        open={pendingDisable !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDisable(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingDisable?.kind === 'profile'
                ? 'Отключить профиль?'
                : 'Отключить привязку?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDisable?.kind === 'profile'
                ? `Новые SQL-запросы не смогут использовать «${pendingDisable.item.name}». Активных привязок: ${activeBindings.length}${
                    activeBindings.length
                      ? ` (${activeBindings
                          .slice(0, 3)
                          .map(
                            (binding) =>
                              `${binding.catalog_service}/${binding.catalog_database}/${binding.catalog_schema}`,
                          )
                          .join(
                            ', ',
                          )}${activeBindings.length > 3 ? ` и ещё ${activeBindings.length - 3}` : ''})`
                      : ''
                  }.`
                : 'Новые SQL-запросы не смогут использовать эту привязку схемы.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingDisable?.kind === 'profile')
                  void toggleProfile(pendingDisable.item);
                else if (pendingDisable?.kind === 'binding')
                  void toggleBinding(pendingDisable.item);
                setPendingDisable(null);
              }}
            >
              Отключить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </main>
  );
}

export default function ExecutionRegistryPage() {
  const { data: userInfo, loading: userInfoLoading } = useFetchUserInfo();
  const actorId = userInfo.id?.trim();
  const accessQuery = useQuery({
    queryKey: ['business-document-capabilities', actorId],
    queryFn: getBusinessDocumentCapabilities,
    enabled: Boolean(actorId) && !userInfoLoading,
    retry: false,
  });
  if (userInfoLoading || (actorId && accessQuery.isPending)) {
    return <main className="p-8 text-text-secondary">Проверяем доступ…</main>;
  }
  if (accessQuery.data?.access_role !== 'ADMIN') {
    return (
      <main className="p-8" role="alert">
        Источники SQL доступны только администратору.{' '}
        <Link to={Routes.SqlProjects}>Вернуться к SQL-проектам</Link>
      </main>
    );
  }
  return <ExecutionRegistryWorkspace />;
}
