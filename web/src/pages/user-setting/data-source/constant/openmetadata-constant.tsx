import { FormFieldConfig, FormFieldType } from '@/components/dynamic-form';
import { TFunction } from 'i18next';

export const openMetadataConstant = (t: TFunction): FormFieldConfig[] => [
  {
    label: t('setting.connectorFields.openmetadataApiBaseUrl'),
    name: 'config.base_url',
    type: FormFieldType.Text,
    required: true,
    placeholder: 'http://host.docker.internal:8585',
    tooltip: t('setting.openMetadataServerUrlTip'),
  },
  {
    label: t('setting.connectorFields.openmetadataPublicUrl'),
    name: 'config.public_url',
    type: FormFieldType.Text,
    required: false,
    placeholder: 'http://127.0.0.1:8585',
    tooltip: t(
      'setting.connectorMessages.browserUrlUsedInCitationsDefaultsToTheApiUrl',
    ),
  },
  {
    label: t('setting.connectorFields.username'),
    name: 'config.credentials.openmetadata_username',
    type: FormFieldType.Text,
    required: false,
    placeholder: 'admin@example.com',
    tooltip: t(
      'setting.connectorMessages.useUsernameAndPasswordOrProvideAJwtTokenBelow',
    ),
  },
  {
    label: t('setting.connectorFields.password'),
    name: 'config.credentials.openmetadata_password',
    type: FormFieldType.Password,
    required: false,
    placeholder: t(
      'setting.connectorMessages.leaveBlankWhenEditingToKeepTheSavedPassword',
    ),
  },
  {
    label: t('setting.connectorFields.jwtToken'),
    name: 'config.credentials.openmetadata_jwt_token',
    type: FormFieldType.Password,
    required: false,
    placeholder: t(
      'setting.connectorMessages.alternativeToUsernameAndPassword',
    ),
  },
  {
    label: t('setting.connectorFields.services'),
    name: 'config.services',
    type: FormFieldType.Tag,
    required: false,
    tooltip: t(
      'setting.connectorMessages.optionalExactServiceNamesEmptyMeansAllDatabaseServices',
    ),
  },
  {
    label: t('setting.connectorFields.domains'),
    name: 'config.domains',
    type: FormFieldType.Tag,
    required: false,
    tooltip: t(
      'setting.connectorMessages.optionalExactDomainNamesTheDatasetMustRemainPrivate',
    ),
  },
  {
    label: t('setting.connectorFields.tags'),
    name: 'config.tags',
    type: FormFieldType.Tag,
    required: false,
    tooltip: t('setting.connectorMessages.optionalExactOpenmetadataTags'),
  },
  {
    label: t('setting.connectorFields.includeColumns'),
    name: 'config.include_columns',
    type: FormFieldType.Checkbox,
    required: false,
  },
  {
    label: t('setting.connectorFields.batchSize'),
    name: 'config.batch_size',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 1,
      max: 1000,
      message: t('setting.connectorMessages.batchSizeMustBeBetween1And1000'),
    },
  },
  {
    label: t('setting.connectorFields.maximumTables'),
    name: 'config.max_entities',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 1,
      max: 100000,
      message: t(
        'setting.connectorMessages.maximumTablesMustBeBetween1And100000',
      ),
    },
  },
  {
    label: t('setting.connectorFields.requestTimeoutSeconds'),
    name: 'config.timeout_seconds',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 1,
      max: 120,
      message: t(
        'setting.connectorMessages.timeoutMustBeBetween1And120Seconds',
      ),
    },
  },
  {
    label: t('setting.connectorFields.retryCount'),
    name: 'config.retry_count',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 0,
      max: 10,
      message: t('setting.connectorMessages.retryCountMustBeBetween0And10'),
    },
  },
];
