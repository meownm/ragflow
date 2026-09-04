import { FormFieldConfig, FormFieldType } from '@/components/dynamic-form';
import { TFunction } from 'i18next';
import EvaWikiProjectField from '../component/eva-wiki-project-field';

export const evaWikiConstant = (t: TFunction): FormFieldConfig[] => [
  {
    label: t('setting.evaApiBaseUrl', {
      defaultValue: 'EVA API Base URL',
    }),
    name: 'config.api_base_url',
    type: FormFieldType.Text,
    required: true,
    placeholder: 'https://eva.example.com',
    validation: {
      message: t('setting.evaApiBaseUrlRequired', {
        defaultValue: 'EVA API Base URL is required',
      }),
    },
    tooltip: t('setting.evaApiBaseUrlTip', {
      defaultValue:
        'URL reachable from the Агент Раггер server. For a local Docker service, use the Docker host gateway URL.',
    }),
  },
  {
    label: t('setting.evaWebBaseUrl', {
      defaultValue: 'EVA Web Base URL',
    }),
    name: 'config.web_base_url',
    type: FormFieldType.Text,
    required: false,
    placeholder: 'https://eva.example.com',
    tooltip: t('setting.evaWebBaseUrlTip', {
      defaultValue:
        'Public browser URL used in citations. Defaults to the API base URL.',
    }),
  },
  {
    label: t('setting.evaConnectorApiToken', {
      defaultValue: 'EVA API Token',
    }),
    name: 'config.credentials.eva_api_token',
    type: FormFieldType.Password,
    required: false,
    placeholder: t('setting.evaConnectorTokenPlaceholder', {
      defaultValue:
        'Required when creating; leave blank to keep the saved token',
    }),
    customValidate: (value, formValues) =>
      formValues?.id || String(value || '').trim()
        ? true
        : t('setting.evaConnectorTokenRequired', {
            defaultValue: 'EVA API Token is required',
          }),
  },
  {
    label: t('setting.evaProject', { defaultValue: 'EVA Project' }),
    name: 'config.project_id',
    type: FormFieldType.Custom,
    required: true,
    validation: {
      message: t('setting.evaProjectRequired', {
        defaultValue: 'EVA Project is required',
      }),
    },
    render: (field) => <EvaWikiProjectField field={field} />,
    tooltip: t('setting.evaProjectTip', {
      defaultValue:
        'Select one project accessible to the configured EVA token. The project cannot be changed after the connector is created. EVA connectors can only be linked to a private Агент Раггер knowledge base.',
    }),
  },
  {
    label: t('setting.evaIncludeAttachments', {
      defaultValue: 'Include Attachments',
    }),
    name: 'config.include_attachments',
    type: FormFieldType.Checkbox,
    required: false,
  },
  {
    label: t('setting.evaVerifySslCertificate', {
      defaultValue: 'Verify SSL Certificate',
    }),
    name: 'config.verify_ssl',
    type: FormFieldType.Checkbox,
    required: false,
  },
  {
    label: t('setting.evaIncludeArchivedPages', {
      defaultValue: 'Include Archived Pages',
    }),
    name: 'config.include_archived',
    type: FormFieldType.Checkbox,
    required: false,
  },
  {
    label: t('setting.evaBatchSize', { defaultValue: 'Batch Size' }),
    name: 'config.batch_size',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 1,
      max: 1000,
      message: t('setting.evaBatchSizeValidation', {
        defaultValue: 'Batch Size must be between 1 and 1000',
      }),
    },
  },
  {
    label: t('setting.evaAttachmentSizeLimit', {
      defaultValue: 'Attachment Size Limit (bytes)',
    }),
    name: 'config.attachment_size_limit',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 1,
      max: 104857600,
      message: t('setting.evaAttachmentSizeValidation', {
        defaultValue: 'Attachment limit must be between 1 byte and 100 MiB',
      }),
    },
  },
  {
    label: t('setting.evaPageSizeLimit', {
      defaultValue: 'Page Size Limit (bytes)',
    }),
    name: 'config.page_size_limit',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 1,
      max: 104857600,
      message: t('setting.evaPageSizeValidation', {
        defaultValue: 'Page limit must be between 1 byte and 100 MiB',
      }),
    },
  },
  {
    label: t('setting.evaRetryCount', { defaultValue: 'Retry Count' }),
    name: 'config.retry_count',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 0,
      max: 10,
      message: t('setting.evaRetryCountValidation', {
        defaultValue: 'Retry Count must be between 0 and 10',
      }),
    },
  },
];
