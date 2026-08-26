import { FormFieldConfig, FormFieldType } from '@/components/dynamic-form';
import EvaWikiProjectField from '../component/eva-wiki-project-field';

export const evaWikiConstant: FormFieldConfig[] = [
  {
    label: 'EVA API Base URL',
    name: 'config.api_base_url',
    type: FormFieldType.Text,
    required: true,
    placeholder: 'https://eva.example.com',
    tooltip:
      'URL reachable from the RAGFlow server. For a local Docker service, use the Docker host gateway URL.',
  },
  {
    label: 'EVA Web Base URL',
    name: 'config.web_base_url',
    type: FormFieldType.Text,
    required: false,
    placeholder: 'https://eva.example.com',
    tooltip:
      'Public browser URL used in citations. Defaults to the API base URL.',
  },
  {
    label: 'EVA API Token',
    name: 'config.credentials.eva_api_token',
    type: FormFieldType.Password,
    required: false,
    placeholder: 'Required when creating; leave blank to keep the saved token',
    customValidate: (value, formValues) =>
      formValues?.id || String(value || '').trim()
        ? true
        : 'EVA API Token is required',
  },
  {
    label: 'EVA Project',
    name: 'config.project_id',
    type: FormFieldType.Custom,
    required: true,
    render: (field) => <EvaWikiProjectField field={field} />,
    tooltip:
      'Select one project accessible to the configured EVA token. The project cannot be changed after the connector is created. EVA connectors can only be linked to a private RAGFlow knowledge base.',
  },
  {
    label: 'Include Attachments',
    name: 'config.include_attachments',
    type: FormFieldType.Checkbox,
    required: false,
  },
  {
    label: 'Verify SSL Certificate',
    name: 'config.verify_ssl',
    type: FormFieldType.Checkbox,
    required: false,
  },
  {
    label: 'Include Archived Pages',
    name: 'config.include_archived',
    type: FormFieldType.Checkbox,
    required: false,
  },
  {
    label: 'Batch Size',
    name: 'config.batch_size',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 1,
      max: 1000,
      message: 'Batch Size must be between 1 and 1000',
    },
  },
  {
    label: 'Attachment Size Limit (bytes)',
    name: 'config.attachment_size_limit',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 1,
      max: 104857600,
      message: 'Attachment limit must be between 1 byte and 100 MiB',
    },
  },
  {
    label: 'Page Size Limit (bytes)',
    name: 'config.page_size_limit',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 1,
      max: 104857600,
      message: 'Page limit must be between 1 byte and 100 MiB',
    },
  },
  {
    label: 'Retry Count',
    name: 'config.retry_count',
    type: FormFieldType.Number,
    required: false,
    validation: {
      min: 0,
      max: 10,
      message: 'Retry Count must be between 0 and 10',
    },
  },
];
