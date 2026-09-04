import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { SelectWithSearch } from '@/components/originui/select-with-search';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import message from '@/components/ui/message';
import {
  getBusinessDocumentsSettings,
  setBusinessDocumentsSettings,
} from '@/services/admin-service';

const BusinessDocumentsSettingsKeys = {
  detail: ['admin', 'business-documents-settings'] as const,
};

function AdminBusinessDocumentsSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [connectorId, setConnectorId] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: BusinessDocumentsSettingsKeys.detail,
    queryFn: async () => (await getBusinessDocumentsSettings()).data.data,
  });

  useEffect(() => {
    if (data) {
      setConnectorId(data.eva_connector_id || '');
    }
  }, [data]);

  const options = useMemo(
    () =>
      (data?.eva_spaces || []).map((space) => ({
        value: space.connector_id,
        label: space.project_name,
      })),
    [data?.eva_spaces],
  );
  const isDirty = connectorId !== (data?.eva_connector_id || '');

  const mutation = useMutation({
    mutationFn: () => setBusinessDocumentsSettings(connectorId || null),
    onSuccess: async ({ data: response }) => {
      setConnectorId(response.data.eva_connector_id || '');
      await queryClient.invalidateQueries({
        queryKey: BusinessDocumentsSettingsKeys.detail,
      });
      message.success(t('admin.businessDocumentsSettingsPage.saved'));
    },
  });

  return (
    <Card
      className="h-full overflow-y-auto rounded-xl border-0.5 border-border-button bg-transparent !shadow-none"
      data-testid="business-documents-settings-admin"
    >
      <CardHeader className="border-b border-border-button">
        <CardTitle>{t('admin.businessDocumentsSettings')}</CardTitle>
        <CardDescription>
          {t('admin.businessDocumentsSettingsPage.description')}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-6 pt-6">
        <div className="max-w-2xl space-y-2">
          <label className="text-sm font-medium" htmlFor="eva-space-select">
            {t('admin.businessDocumentsSettingsPage.evaSpace')}
          </label>
          <SelectWithSearch
            value={connectorId}
            onChange={setConnectorId}
            options={options}
            allowClear
            disabled={
              isLoading ||
              mutation.isPending ||
              (options.length === 0 && !connectorId)
            }
            placeholder={t(
              'admin.businessDocumentsSettingsPage.evaSpacePlaceholder',
            )}
            emptyData={t('admin.businessDocumentsSettingsPage.evaSpacesEmpty')}
            testId="business-documents-eva-space-select"
            optionTestIdPrefix="business-documents-eva-space-option-"
          />
          <p className="text-sm text-text-secondary">
            {t('admin.businessDocumentsSettingsPage.evaSpaceHelp')}
          </p>
          {data?.eva_connector_id && !data.selected_space_available && (
            <p className="text-sm text-state-error" role="alert">
              {t('admin.businessDocumentsSettingsPage.selectedUnavailable')}
            </p>
          )}
          {!isLoading && options.length === 0 && (
            <p className="text-sm text-state-error" role="alert">
              {t('admin.businessDocumentsSettingsPage.createConnectorFirst')}
            </p>
          )}
        </div>

        <div className="flex justify-end">
          <Button
            data-testid="business-documents-settings-save"
            onClick={() => mutation.mutate()}
            disabled={!isDirty || mutation.isPending}
          >
            {mutation.isPending
              ? t('admin.businessDocumentsSettingsPage.saving')
              : t('admin.businessDocumentsSettingsPage.save')}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default AdminBusinessDocumentsSettings;
