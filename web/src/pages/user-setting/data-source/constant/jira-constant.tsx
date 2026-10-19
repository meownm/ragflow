import { FormFieldType } from '@/components/dynamic-form';
import { TFunction } from 'i18next';

export const jiraConstant = (t: TFunction) => [
  {
    label: t('setting.connectorFields.jiraUserEmail'),
    name: 'config.credentials.jira_user_email',
    type: FormFieldType.Text,
    required: true,
    placeholder: 'you@example.com',
    tooltip: t('setting.jiraEmailTip'),
    shouldRender: (formValues: any) => formValues?.config?.is_cloud !== false,
    customValidate: (val: string, formValues: any) => {
      if (formValues?.config?.is_cloud !== false) {
        return (
          Boolean(val) || t('setting.connectorMessages.jiraUserEmailIsRequired')
        );
      }
      return true;
    },
  },
  {
    label: t('setting.connectorFields.jiraUsername'),
    name: 'config.credentials.jira_username',
    type: FormFieldType.Text,
    required: true,
    tooltip: t('setting.jiraEmailTip'),
    shouldRender: (formValues: any) => formValues?.config?.is_cloud === false,
    customValidate: (val: string, formValues: any) => {
      if (formValues?.config?.is_cloud === false) {
        return (
          Boolean(val) || t('setting.connectorMessages.jiraUsernameIsRequired')
        );
      }
      return true;
    },
  },
  {
    label: t('setting.connectorFields.jiraBaseUrl'),
    name: 'config.base_url',
    type: FormFieldType.Text,
    required: true,
    placeholder: 'https://your-domain.atlassian.net',
    tooltip: t('setting.jiraBaseUrlTip'),
  },
  {
    label: t('setting.connectorFields.projectKey'),
    name: 'config.project_key',
    type: FormFieldType.Text,
    required: false,
    placeholder: 'Агент Раггер',
    tooltip: t('setting.jiraProjectKeyTip'),
  },
  {
    label: t('setting.connectorFields.customJql'),
    name: 'config.jql_query',
    type: FormFieldType.Textarea,
    required: false,
    placeholder: 'project = RAG AND updated >= -7d',
    tooltip: t('setting.jiraJqlTip'),
  },
  {
    label: t('setting.connectorFields.batchSize'),
    name: 'config.batch_size',
    type: FormFieldType.Number,
    required: false,
    tooltip: t('setting.jiraBatchSizeTip'),
  },
  {
    label: t('setting.connectorFields.attachmentSizeLimitBytes'),
    name: 'config.attachment_size_limit',
    type: FormFieldType.Number,
    required: false,
    defaultValue: 10 * 1024 * 1024,
    tooltip: t('setting.jiraAttachmentSizeTip'),
  },
  {
    label: t('setting.connectorFields.labelsToSkip'),
    name: 'config.labels_to_skip',
    type: FormFieldType.Tag,
    required: false,
    tooltip: t('setting.jiraLabelsTip'),
  },
  {
    label: t('setting.connectorFields.commentEmailBlacklist'),
    name: 'config.comment_email_blacklist',
    type: FormFieldType.Tag,
    required: false,
    tooltip: t('setting.jiraBlacklistTip'),
  },
  {
    label: t('setting.connectorFields.includeComments'),
    name: 'config.include_comments',
    type: FormFieldType.Checkbox,
    required: false,
    defaultValue: true,
    tooltip: t('setting.jiraCommentsTip'),
  },
  {
    label: t('setting.connectorFields.includeAttachments'),
    name: 'config.include_attachments',
    type: FormFieldType.Checkbox,
    required: false,
    defaultValue: false,
    tooltip: t('setting.jiraAttachmentsTip'),
  },
  {
    label: t('setting.connectorFields.mode'),
    name: 'config.is_cloud',
    type: FormFieldType.Segmented,
    options: [
      { label: t('setting.connectorFields.cloud'), value: true },
      { label: t('setting.connectorFields.server'), value: false },
    ],
    defaultValue: true,
  },
  {
    label: t('setting.connectorFields.jiraApiToken'),
    name: 'config.credentials.jira_api_token',
    type: FormFieldType.Password,
    required: false,
    tooltip: t('setting.jiraTokenTip'),
    shouldRender: (formValues: any) => formValues?.config?.is_cloud !== false,
    customValidate: (val: string, formValues: any) => {
      if (formValues?.config?.is_cloud !== false) {
        return (
          Boolean(val) || t('setting.connectorMessages.jiraApiTokenIsRequired')
        );
      }
      return true;
    },
  },
  {
    label: t('setting.connectorFields.jiraPassword'),
    name: 'config.credentials.jira_password',
    type: FormFieldType.Password,
    required: false,
    tooltip: t('setting.jiraPasswordTip'),
    shouldRender: (formValues: any) => formValues?.config?.is_cloud === false,
    customValidate: (val: string, formValues: any) => {
      if (formValues?.config?.is_cloud === false) {
        return (
          Boolean(val) || t('setting.connectorMessages.jiraPasswordIsRequired')
        );
      }
      return true;
    },
  },
  {
    label: t('setting.connectorFields.useScopedToken'),
    name: 'config.scoped_token',
    type: FormFieldType.Checkbox,
    required: false,
    tooltip: t('setting.jiraScopedTokenTip'),
    shouldRender: (formValues: any) => formValues?.config?.is_cloud !== false,
  },
];
