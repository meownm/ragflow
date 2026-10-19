import { FormFieldConfig, FormFieldType } from '@/components/dynamic-form';
import { IconFontFill } from '@/components/icon-font';
import SvgIcon from '@/components/svg-icon';
import { TFunction } from 'i18next';
import { DatabaseZap, Mail, Rss } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import BoxTokenField from '../component/box-token-field';
import GmailTokenField from '../component/gmail-token-field';
import GoogleDriveTokenField from '../component/google-drive-token-field';
import { IDataSourceInfoMap } from '../interface';
import { bitbucketConstant } from './bitbucket-constant';
import { confluenceConstant } from './confluence-constant';
import { evaWikiConstant } from './eva-wiki-constant';
import { jiraConstant } from './jira-constant';
import { openMetadataConstant } from './openmetadata-constant';
import { S3Constant } from './s3-constant';
import { seafileConstant } from './seafile-constant';

export enum DataSourceKey {
  CONFLUENCE = 'confluence',
  EVA_WIKI = 'eva_wiki',
  OPENMETADATA = 'openmetadata',
  NOTION = 'notion',
  GOOGLE_DRIVE = 'google_drive',
  GMAIL = 'gmail',
  GOOGLE_CLOUD_STORAGE = 'google_cloud_storage',
  OCI_STORAGE = 'oci_storage',
  S3 = 's3',
  R2 = 'r2',
  JIRA = 'jira',
  BOX = 'box',
  DROPBOX = 'dropbox',
  BITBUCKET = 'bitbucket',
  GITLAB = 'gitlab',
  GITHUB = 'github',
  MOODLE = 'moodle',
  DISCORD = 'discord',
  ZENDESK = 'zendesk',
  WEBDAV = 'webdav',
  AIRTABLE = 'airtable',
  ASANA = 'asana',
  IMAP = 'imap',
  DINGTALK_AI_TABLE = 'dingtalk_ai_table',
  SEAFILE = 'seafile',
  MYSQL = 'mysql',
  POSTGRESQL = 'postgresql',
  BIGQUERY = 'bigquery',
  REST_API = 'rest_api',
  RSS = 'rss',
  ONEDRIVE = 'onedrive',
  OUTLOOK = 'outlook',
  SALESFORCE = 'salesforce',
  AZURE_BLOB = 'azure_blob',
  TEAMS = 'teams',
  SLACK = 'slack',
  SHAREPOINT = 'sharepoint',
}

type DataSourceFeatureVisibility = {
  syncDeletedFiles?: boolean;
};

type DataSourceFormValues = Record<string, any>;

export const DataSourceFeatureVisibilityMap: Partial<
  Record<DataSourceKey, DataSourceFeatureVisibility>
> = {
  [DataSourceKey.GITHUB]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.GITLAB]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.GOOGLE_DRIVE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.GMAIL]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.IMAP]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.CONFLUENCE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.EVA_WIKI]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.OPENMETADATA]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.BOX]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.DROPBOX]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.S3]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.R2]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.GOOGLE_CLOUD_STORAGE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.OCI_STORAGE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.NOTION]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.DISCORD]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.JIRA]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.BITBUCKET]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.AIRTABLE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.DINGTALK_AI_TABLE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.WEBDAV]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.ZENDESK]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.SEAFILE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.ASANA]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.RSS]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.MOODLE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.ONEDRIVE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.OUTLOOK]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.SALESFORCE]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.AZURE_BLOB]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.TEAMS]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.SLACK]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.SHAREPOINT]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.MYSQL]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.POSTGRESQL]: {
    syncDeletedFiles: true,
  },
  [DataSourceKey.BIGQUERY]: {
    syncDeletedFiles: true,
  },
};

const isDataSourceFeatureVisible = (
  source?: DataSourceKey,
  feature?: keyof DataSourceFeatureVisibility,
) => {
  if (!source || !feature) {
    return false;
  }

  return Boolean(DataSourceFeatureVisibilityMap[source]?.[feature]);
};

export const generateDataSourceInfo = (t: TFunction) => {
  return {
    [DataSourceKey.RSS]: {
      name: 'RSS',
      description: t(`setting.${DataSourceKey.RSS}Description`),
      icon: <Rss className="text-text-primary" size={22} />,
    },
    [DataSourceKey.GOOGLE_CLOUD_STORAGE]: {
      name: 'Google Cloud Storage',
      description: t(
        `setting.${DataSourceKey.GOOGLE_CLOUD_STORAGE}Description`,
      ),
      icon: <SvgIcon name={'data-source/google-cloud-storage'} width={38} />,
    },
    [DataSourceKey.OCI_STORAGE]: {
      name: 'Oracle Storage',
      description: t(`setting.${DataSourceKey.OCI_STORAGE}Description`),
      icon: <SvgIcon name={'data-source/oracle-storage'} width={38} />,
    },
    [DataSourceKey.R2]: {
      name: 'R2',
      description: t(`setting.${DataSourceKey.R2}Description`),
      icon: <SvgIcon name={'data-source/r2'} width={38} />,
    },
    [DataSourceKey.S3]: {
      name: 'S3',
      description: t(`setting.${DataSourceKey.S3}Description`),
      icon: <SvgIcon name={'data-source/s3'} width={38} />,
    },
    [DataSourceKey.NOTION]: {
      name: 'Notion',
      description: t(`setting.${DataSourceKey.NOTION}Description`),
      icon: <SvgIcon name={'data-source/notion'} width={38} />,
    },
    [DataSourceKey.DISCORD]: {
      name: 'Discord',
      description: t(`setting.${DataSourceKey.DISCORD}Description`),
      icon: <SvgIcon name={'data-source/discord'} width={38} />,
    },
    [DataSourceKey.CONFLUENCE]: {
      name: 'Confluence',
      description: t(`setting.${DataSourceKey.CONFLUENCE}Description`),
      icon: <SvgIcon name={'data-source/confluence'} width={38} />,
    },
    [DataSourceKey.EVA_WIKI]: {
      name: 'EVA Wiki',
      description: t(`setting.${DataSourceKey.EVA_WIKI}Description`),
      icon: <SvgIcon name={'data-source/eva-wiki'} width={38} />,
    },
    [DataSourceKey.OPENMETADATA]: {
      name: 'OpenMetadata',
      description: t(`setting.${DataSourceKey.OPENMETADATA}Description`),
      icon: <DatabaseZap className="text-text-primary" size={24} />,
    },
    [DataSourceKey.GOOGLE_DRIVE]: {
      name: 'Google Drive',
      description: t(`setting.${DataSourceKey.GOOGLE_DRIVE}Description`),
      icon: <SvgIcon name={'data-source/google-drive'} width={38} />,
    },
    [DataSourceKey.GMAIL]: {
      name: 'Gmail',
      description: t(`setting.${DataSourceKey.GMAIL}Description`),
      icon: <SvgIcon name={'data-source/gmail'} width={38} />,
    },
    [DataSourceKey.REST_API]: {
      name: 'REST API',
      description: t(`setting.${DataSourceKey.REST_API}Description`),
      icon: <SvgIcon name={'data-source/rest-api'} width={38} />,
    },
    [DataSourceKey.MOODLE]: {
      name: 'Moodle',
      description: t(`setting.${DataSourceKey.MOODLE}Description`),
      icon: <SvgIcon name={'data-source/moodle'} width={38} />,
    },
    [DataSourceKey.TEAMS]: {
      name: 'Microsoft Teams',
      description: t(`setting.${DataSourceKey.TEAMS}Description`),
      icon: <SvgIcon name={'data-source/teams'} width={38} />,
    },
    [DataSourceKey.SLACK]: {
      name: 'Slack',
      description: t(`setting.${DataSourceKey.SLACK}Description`),
      icon: <SvgIcon name={'data-source/slack'} width={38} />,
    },
    [DataSourceKey.SHAREPOINT]: {
      name: 'SharePoint',
      description: t(`setting.${DataSourceKey.SHAREPOINT}Description`),
      icon: <SvgIcon name={'data-source/sharepoint'} width={38} />,
    },
    [DataSourceKey.JIRA]: {
      name: 'Jira',
      description: t(`setting.${DataSourceKey.JIRA}Description`),
      icon: <SvgIcon name={'data-source/jira'} width={38} />,
    },
    [DataSourceKey.WEBDAV]: {
      name: 'WebDAV',
      description: t(`setting.${DataSourceKey.WEBDAV}Description`),
      icon: <SvgIcon name={'data-source/webdav'} width={38} />,
    },
    [DataSourceKey.DROPBOX]: {
      name: 'Dropbox',
      description: t(`setting.${DataSourceKey.DROPBOX}Description`),
      icon: <SvgIcon name={'data-source/dropbox'} width={38} />,
    },
    [DataSourceKey.BOX]: {
      name: 'Box',
      description: t(`setting.${DataSourceKey.BOX}Description`),
      icon: <SvgIcon name={'data-source/box'} width={38} />,
    },
    [DataSourceKey.AIRTABLE]: {
      name: 'Airtable',
      description: t(`setting.${DataSourceKey.AIRTABLE}Description`),
      icon: <SvgIcon name={'data-source/airtable'} width={38} />,
    },
    [DataSourceKey.DINGTALK_AI_TABLE]: {
      name: 'Dingtalk AI Table',
      description: t(`setting.dingtalkAITableDescription`),
      icon: <SvgIcon name={'data-source/dingtalk-ai-table'} width={38} />,
    },
    [DataSourceKey.GITLAB]: {
      name: 'GitLab',
      description: t(`setting.${DataSourceKey.GITLAB}Description`),
      icon: <SvgIcon name={'data-source/gitlab'} width={38} />,
    },
    [DataSourceKey.ASANA]: {
      name: 'Asana',
      description: t(`setting.${DataSourceKey.ASANA}Description`),
      icon: <SvgIcon name={'data-source/asana'} width={38} />,
    },
    [DataSourceKey.GITHUB]: {
      name: 'GitHub',
      description: t(`setting.${DataSourceKey.GITHUB}Description`),
      icon: (
        <IconFontFill
          // name="a-DiscordIconSVGVectorIcon"
          name="GitHub"
          className="text-text-primary size-6"
        ></IconFontFill>
      ),
    },
    [DataSourceKey.IMAP]: {
      name: 'IMAP',
      description: t(`setting.${DataSourceKey.IMAP}Description`),
      icon: <Mail className="text-text-primary" size={22} />,
    },
    [DataSourceKey.BITBUCKET]: {
      name: 'Bitbucket',
      description: t(`setting.${DataSourceKey.BITBUCKET}Description`),
      icon: <SvgIcon name={'data-source/bitbucket'} width={38} />,
    },
    [DataSourceKey.ZENDESK]: {
      name: 'Zendesk',
      description: t(`setting.${DataSourceKey.ZENDESK}Description`),
      icon: <SvgIcon name={'data-source/zendesk'} width={38} />,
    },
    [DataSourceKey.SEAFILE]: {
      name: 'SeaFile',
      description: t(`setting.${DataSourceKey.SEAFILE}Description`),
      icon: <SvgIcon name={'data-source/seafile'} width={38} />,
    },
    [DataSourceKey.MYSQL]: {
      name: 'MySQL',
      description: t(`setting.${DataSourceKey.MYSQL}Description`),
      icon: <SvgIcon name={'data-source/mysql'} width={38} />,
    },
    [DataSourceKey.POSTGRESQL]: {
      name: 'PostgreSQL',
      description: t(`setting.${DataSourceKey.POSTGRESQL}Description`),
      icon: <SvgIcon name={'data-source/postgresql'} width={38} />,
    },
    [DataSourceKey.BIGQUERY]: {
      name: 'BigQuery',
      description: t(`setting.${DataSourceKey.BIGQUERY}Description`),
      icon: <SvgIcon name={'data-source/bigquery'} width={38} />,
    },
    [DataSourceKey.ONEDRIVE]: {
      name: 'OneDrive',
      description: t(`setting.${DataSourceKey.ONEDRIVE}Description`),
      icon: <SvgIcon name={'data-source/onedrive'} width={38} />,
    },
    [DataSourceKey.OUTLOOK]: {
      name: 'Outlook',
      description: t(`setting.${DataSourceKey.OUTLOOK}Description`),
      icon: <Mail className="text-text-primary" size={22} />,
    },
    [DataSourceKey.SALESFORCE]: {
      name: 'Salesforce',
      description: t(`setting.${DataSourceKey.SALESFORCE}Description`),
      icon: <SvgIcon name={'data-source/salesforce'} width={38} />,
    },
    [DataSourceKey.AZURE_BLOB]: {
      name: 'Azure Blob Storage',
      description: t(`setting.${DataSourceKey.AZURE_BLOB}Description`),
      icon: <SvgIcon name={'data-source/azure-blob'} width={38} />,
    },
  };
};

export const useDataSourceInfo = () => {
  const { t } = useTranslation();
  const [dataSourceInfo, setDataSourceInfo] = useState<IDataSourceInfoMap>(
    generateDataSourceInfo(t) as IDataSourceInfoMap,
  );
  useEffect(() => {
    setDataSourceInfo(generateDataSourceInfo(t));
  }, [t]);
  return { dataSourceInfo };
};

const isPlainObject = (value: unknown): value is DataSourceFormValues =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export const mergeDataSourceFormValues = (
  ...values: Array<DataSourceFormValues | undefined>
): DataSourceFormValues =>
  values.reduce<DataSourceFormValues>((result, current) => {
    if (!current) {
      return result;
    }

    const next = { ...result };

    Object.entries(current).forEach(([key, value]) => {
      if (isPlainObject(value) && isPlainObject(next[key])) {
        next[key] = mergeDataSourceFormValues(next[key], value);
      } else {
        next[key] = value;
      }
    });

    return next;
  }, {});

export const getDataSourceBaseFields = (t: TFunction) => [
  {
    id: 'Id',
    name: 'id',
    type: FormFieldType.Text,
    required: false,
    hidden: true,
  },
  {
    label: t('setting.connectorFields.name'),
    name: 'name',
    type: FormFieldType.Text,
    required: true,
    tooltip: t('setting.connectorNameTip'),
  },
  {
    label: t('setting.connectorFields.source'),
    name: 'source',
    type: FormFieldType.Select,
    required: true,
    hidden: true,
    options: Object.keys(DataSourceKey).map((item) => ({
      label: item,
      value: DataSourceKey[item as keyof typeof DataSourceKey],
    })),
  },
];

export const getCommonExtraFields = (
  source: DataSourceKey | undefined,
  t: TFunction,
): FormFieldConfig[] => [
  {
    label: t('setting.syncDeletedFiles'),
    name: 'config.sync_deleted_files',
    type: FormFieldType.Checkbox,
    required: false,
    defaultValue: false,
    shouldRender: () => isDataSourceFeatureVisible(source, 'syncDeletedFiles'),
  },
];

export const getCommonExtraDefaultValues = () => ({
  config: {
    sync_deleted_files: false,
  },
});

const getDataSourceFormFields = (t: TFunction) => ({
  [DataSourceKey.ONEDRIVE]: [
    {
      label: t('setting.connectorFields.tenantId'),
      name: 'config.credentials.tenant_id',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
      tooltip: t('setting.onedriveTenantIdTip'),
    },
    {
      label: t('setting.connectorFields.clientId'),
      name: 'config.credentials.client_id',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
      tooltip: t('setting.onedriveClientIdTip'),
    },
    {
      label: t('setting.connectorFields.clientSecret'),
      name: 'config.credentials.client_secret',
      type: FormFieldType.Password,
      required: true,
      tooltip: t('setting.onedriveClientSecretTip'),
    },
    {
      label: t('setting.connectorFields.folderPathOptional'),
      name: 'config.folder_path',
      type: FormFieldType.Text,
      required: false,
      placeholder: '/Documents/Reports',
      tooltip: t('setting.onedriveFolderPathTip'),
    },
    {
      label: t('setting.connectorFields.batchSize'),
      name: 'config.batch_size',
      type: FormFieldType.Number,
      required: false,
      validation: {
        min: 1,
        message: t('setting.connectorMessages.batchSizeMustBeAtLeast1'),
      },
    },
  ],
  [DataSourceKey.OUTLOOK]: [
    {
      label: t('setting.connectorFields.tenantId'),
      name: 'config.credentials.tenant_id',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
      tooltip: t('setting.outlookTenantIdTip'),
    },
    {
      label: t('setting.connectorFields.clientId'),
      name: 'config.credentials.client_id',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
      tooltip: t('setting.outlookClientIdTip'),
    },
    {
      label: t('setting.connectorFields.clientSecret'),
      name: 'config.credentials.client_secret',
      type: FormFieldType.Password,
      required: true,
      tooltip: t('setting.outlookClientSecretTip'),
    },
    {
      label: t('setting.connectorFields.mailFolder'),
      name: 'config.folder',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'inbox',
      tooltip: t('setting.outlookFolderTip'),
    },
    {
      label: t('setting.connectorFields.mailboxUserIdsOptional'),
      name: 'config.user_ids',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'support@example.com, sales@example.com',
      tooltip: t('setting.outlookUserIdsTip'),
    },
    {
      label: t('setting.connectorFields.batchSize'),
      name: 'config.batch_size',
      type: FormFieldType.Number,
      required: false,
      validation: {
        min: 1,
        message: t('setting.connectorMessages.batchSizeMustBeAtLeast1'),
      },
    },
  ],
  [DataSourceKey.SALESFORCE]: [
    {
      label: t('setting.connectorFields.instanceUrl'),
      name: 'config.credentials.instance_url',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'https://your-domain.my.salesforce.com',
      tooltip: t('setting.salesforceInstanceUrlTip'),
      validation: {
        pattern: /^https:\/\/[a-zA-Z0-9.-]+\.salesforce\.com$/,
        message: t(
          'setting.connectorMessages.mustBeAValidSalesforceDomainHttpsSalesforceCom',
        ),
      },
    },
    {
      label: t('setting.connectorFields.clientId'),
      name: 'config.credentials.client_id',
      type: FormFieldType.Text,
      required: true,
      tooltip: t('setting.salesforceClientIdTip'),
    },
    {
      label: t('setting.connectorFields.clientSecret'),
      name: 'config.credentials.client_secret',
      type: FormFieldType.Password,
      required: true,
      tooltip: t('setting.salesforceClientSecretTip'),
    },
    {
      label: t('setting.connectorFields.objects'),
      name: 'config.objects',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'Account,Contact,Opportunity,Case,Knowledge__kav',
      tooltip: t('setting.salesforceObjectsTip'),
    },
    {
      label: t('setting.connectorFields.apiVersion'),
      name: 'config.api_version',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'v59.0',
      tooltip: t('setting.salesforceApiVersionTip'),
      validation: {
        pattern: /^v\d+\.\d+$/,
        message: t(
          'setting.connectorMessages.apiVersionMustMatchFormatLikeV590',
        ),
      },
    },
    {
      label: t('setting.connectorFields.batchSize'),
      name: 'config.batch_size',
      type: FormFieldType.Number,
      required: false,
      validation: {
        min: 1,
        message: t('setting.connectorMessages.batchSizeMustBeAtLeast1'),
      },
    },
  ],
  [DataSourceKey.AZURE_BLOB]: [
    {
      label: t('setting.connectorFields.authMode'),
      name: 'config.auth_mode',
      type: FormFieldType.Select,
      required: true,
      options: [
        {
          label: t('setting.connectorFields.accountKey'),
          value: 'account_key',
        },
        {
          label: t('setting.connectorFields.connectionString'),
          value: 'connection_string',
        },
        { label: t('setting.connectorFields.sasToken'), value: 'sas_token' },
      ],
      tooltip: t('setting.azureBlobAuthModeTip'),
    },
    {
      label: t('setting.connectorFields.accountName'),
      name: 'config.credentials.account_name',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'mystorageaccount',
      tooltip: t('setting.azureBlobAccountNameTip'),
      shouldRender: (values: any) =>
        values?.config?.auth_mode === 'account_key',
      customValidate: (val: string, values: any) =>
        values?.config?.auth_mode === 'account_key' && !(val ?? '').trim()
          ? t(
              'setting.connectorMessages.accountNameIsRequiredForAccountKeyAuth',
            )
          : true,
    },
    {
      label: t('setting.connectorFields.accountKey'),
      name: 'config.credentials.account_key',
      type: FormFieldType.Password,
      required: false,
      tooltip: t('setting.azureBlobAccountKeyTip'),
      shouldRender: (values: any) =>
        values?.config?.auth_mode === 'account_key',
      customValidate: (val: string, values: any) =>
        values?.config?.auth_mode === 'account_key' && !val
          ? t('setting.connectorMessages.accountKeyIsRequiredForAccountKeyAuth')
          : true,
    },
    {
      label: t('setting.connectorFields.connectionString'),
      name: 'config.credentials.connection_string',
      type: FormFieldType.Password,
      required: false,
      tooltip: t('setting.azureBlobConnectionStringTip'),
      shouldRender: (values: any) =>
        values?.config?.auth_mode === 'connection_string',
      customValidate: (val: string, values: any) =>
        values?.config?.auth_mode === 'connection_string' && !val
          ? t(
              'setting.connectorMessages.connectionStringIsRequiredForConnectionStringAuth',
            )
          : true,
    },
    {
      label: t('setting.connectorFields.containerUrl'),
      name: 'config.credentials.container_url',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'https://account.blob.core.windows.net/container',
      tooltip: t('setting.azureBlobContainerUrlTip'),
      shouldRender: (values: any) => values?.config?.auth_mode === 'sas_token',
      customValidate: (val: string, values: any) =>
        values?.config?.auth_mode === 'sas_token' && !(val ?? '').trim()
          ? t('setting.connectorMessages.containerUrlIsRequiredForSasTokenAuth')
          : true,
    },
    {
      label: t('setting.connectorFields.sasToken'),
      name: 'config.credentials.sas_token',
      type: FormFieldType.Password,
      required: false,
      tooltip: t('setting.azureBlobSasTokenTip'),
      shouldRender: (values: any) => values?.config?.auth_mode === 'sas_token',
      customValidate: (val: string, values: any) =>
        values?.config?.auth_mode === 'sas_token' && !val
          ? t('setting.connectorMessages.sasTokenIsRequiredForSasTokenAuth')
          : true,
    },
    {
      label: t('setting.connectorFields.containerName'),
      name: 'config.credentials.container_name',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'my-container',
      tooltip: t('setting.azureBlobContainerNameTip'),
      shouldRender: (values: any) =>
        values?.config?.auth_mode === 'account_key' ||
        values?.config?.auth_mode === 'connection_string',
      customValidate: (val: string, values: any) => {
        const mode = values?.config?.auth_mode;
        if (
          (mode === 'account_key' || mode === 'connection_string') &&
          !(val ?? '').trim()
        ) {
          return t(
            'setting.connectorMessages.containerNameIsRequiredForThisAuthMode',
          );
        }
        return true;
      },
    },
    {
      label: t('setting.connectorFields.prefixOptional'),
      name: 'config.prefix',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'documents/reports/',
      tooltip: t('setting.azureBlobPrefixTip'),
    },
    {
      label: t('setting.connectorFields.batchSize'),
      name: 'config.batch_size',
      type: FormFieldType.Number,
      required: false,
      validation: {
        min: 1,
        message: t('setting.connectorMessages.batchSizeMustBeAtLeast1'),
      },
    },
  ],
  [DataSourceKey.RSS]: [
    {
      label: t('setting.connectorFields.feedUrl'),
      name: 'config.feed_url',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'https://example.com/feed.xml',
    },
    {
      label: t('setting.connectorFields.batchSize'),
      name: 'config.batch_size',
      type: FormFieldType.Number,
      required: false,
      validation: {
        min: 1,
        message: t('setting.connectorMessages.batchSizeMustBeAtLeast1'),
      },
    },
  ],
  [DataSourceKey.GOOGLE_CLOUD_STORAGE]: [
    {
      label: t('setting.connectorFields.gcsAccessKeyId'),
      name: 'config.credentials.access_key_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.gcsSecretAccessKey'),
      name: 'config.credentials.secret_access_key',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.bucketName'),
      name: 'config.bucket_name',
      type: FormFieldType.Text,
      required: true,
    },
  ],
  [DataSourceKey.OCI_STORAGE]: [
    {
      label: t('setting.connectorFields.ociNamespace'),
      name: 'config.credentials.namespace',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.ociRegion'),
      name: 'config.credentials.region',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.ociAccessKeyId'),
      name: 'config.credentials.access_key_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.ociSecretAccessKey'),
      name: 'config.credentials.secret_access_key',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.bucketName'),
      name: 'config.bucket_name',
      type: FormFieldType.Text,
      required: true,
    },
  ],
  [DataSourceKey.R2]: [
    {
      label: t('setting.connectorFields.r2AccountId'),
      name: 'config.credentials.account_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.r2AccessKeyId'),
      name: 'config.credentials.r2_access_key_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.r2SecretAccessKey'),
      name: 'config.credentials.r2_secret_access_key',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.bucketName'),
      name: 'config.bucket_name',
      type: FormFieldType.Text,
      required: true,
    },
  ],
  [DataSourceKey.S3]: S3Constant(t),
  [DataSourceKey.NOTION]: [
    {
      label: t('setting.connectorFields.notionIntegrationToken'),
      name: 'config.credentials.notion_integration_token',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.rootPageId'),
      name: 'config.root_page_id',
      type: FormFieldType.Text,
      required: false,
    },
  ],
  [DataSourceKey.DISCORD]: [
    {
      label: t('setting.connectorFields.discordBotToken'),
      name: 'config.credentials.discord_bot_token',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.serverIds'),
      name: 'config.server_ids',
      type: FormFieldType.Tag,
      required: false,
    },
    {
      label: t('setting.connectorFields.channels'),
      name: 'config.channels',
      type: FormFieldType.Tag,
      required: false,
    },
  ],

  [DataSourceKey.CONFLUENCE]: confluenceConstant(t),
  [DataSourceKey.EVA_WIKI]: evaWikiConstant(t),
  [DataSourceKey.OPENMETADATA]: openMetadataConstant(t),
  [DataSourceKey.GOOGLE_DRIVE]: [
    {
      label: t('setting.connectorFields.primaryAdminEmail'),
      name: 'config.credentials.google_primary_admin',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'admin@example.com',
      tooltip: t('setting.google_drivePrimaryAdminTip'),
    },
    {
      label: t('setting.connectorFields.oauthTokenJson'),
      name: 'config.credentials.google_tokens',
      type: FormFieldType.Textarea,
      required: true,
      render: (fieldProps: any) => (
        <GoogleDriveTokenField
          value={fieldProps.value}
          onChange={fieldProps.onChange}
        />
      ),
      tooltip: t('setting.google_driveTokenTip'),
    },
    {
      label: t('setting.connectorFields.myDriveEmails'),
      name: 'config.my_drive_emails',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'user1@example.com,user2@example.com',
      tooltip: t('setting.google_driveMyDriveEmailsTip'),
    },
    {
      label: t('setting.connectorFields.sharedFolderUrls'),
      name: 'config.shared_folder_urls',
      type: FormFieldType.Textarea,
      required: true,
      placeholder:
        'https://drive.google.com/drive/folders/XXXXX,https://drive.google.com/drive/folders/YYYYY',
      tooltip: t('setting.google_driveSharedFoldersTip'),
    },
    // The fields below are intentionally disabled for now. Uncomment them when we
    // reintroduce shared drive controls or advanced impersonation options.
    // {
    //   label: 'Shared Drive URLs',
    //   name: 'config.shared_drive_urls',
    //   type: FormFieldType.Text,
    //   required: false,
    //   placeholder:
    //     'Optional: comma-separated shared drive links if you want to include them.',
    // },
    // {
    //   label: 'Specific User Emails',
    //   name: 'config.specific_user_emails',
    //   type: FormFieldType.Text,
    //   required: false,
    //   placeholder:
    //     'Optional: comma-separated list of users to impersonate (overrides defaults).',
    // },
    // {
    //      label: 'Include My Drive',
    //      name: 'config.include_my_drives',
    //      type: FormFieldType.Checkbox,
    //      required: false,
    //      defaultValue: true,
    // },
    // {
    //   label: 'Include Shared Drives',
    //   name: 'config.include_shared_drives',
    //   type: FormFieldType.Checkbox,
    //   required: false,
    //   defaultValue: false,
    // },
    // {
    //   label: 'Include “Shared with me”',
    //   name: 'config.include_files_shared_with_me',
    //   type: FormFieldType.Checkbox,
    //   required: false,
    //   defaultValue: false,
    // },
    // {
    //   label: 'Allow Images',
    //   name: 'config.allow_images',
    //   type: FormFieldType.Checkbox,
    //   required: false,
    //   defaultValue: false,
    // },
    {
      label: '',
      name: 'config.credentials.authentication_method',
      type: FormFieldType.Text,
      required: false,
      hidden: true,
      defaultValue: 'uploaded',
    },
  ],
  [DataSourceKey.GMAIL]: [
    {
      label: t('setting.connectorFields.primaryAdminEmail'),
      name: 'config.credentials.google_primary_admin',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'admin@example.com',
      tooltip: t('setting.gmailPrimaryAdminTip'),
    },
    {
      label: t('setting.connectorFields.oauthTokenJson'),
      name: 'config.credentials.google_tokens',
      type: FormFieldType.Textarea,
      required: true,
      render: (fieldProps: any) => (
        <GmailTokenField
          value={fieldProps.value}
          onChange={fieldProps.onChange}
        />
      ),
      tooltip: t('setting.gmailTokenTip'),
    },
    {
      label: '',
      name: 'config.credentials.authentication_method',
      type: FormFieldType.Text,
      required: false,
      hidden: true,
      defaultValue: 'uploaded',
    },
  ],
  [DataSourceKey.MOODLE]: [
    {
      label: t('setting.connectorFields.moodleUrl'),
      name: 'config.moodle_url',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'https://moodle.example.com',
    },
    {
      label: t('setting.connectorFields.apiToken'),
      name: 'config.credentials.moodle_token',
      type: FormFieldType.Password,
      required: true,
    },
  ],
  [DataSourceKey.TEAMS]: [
    {
      label: t('setting.connectorFields.tenantId'),
      name: 'config.credentials.tenant_id',
      type: FormFieldType.Text,
      required: true,
      tooltip: t('setting.teamsTenantIdTip'),
    },
    {
      label: t('setting.connectorFields.clientId'),
      name: 'config.credentials.client_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.clientSecret'),
      name: 'config.credentials.client_secret',
      type: FormFieldType.Password,
      required: true,
    },
  ],
  [DataSourceKey.SLACK]: [
    {
      label: t('setting.connectorFields.slackBotToken'),
      name: 'config.credentials.slack_bot_token',
      type: FormFieldType.Password,
      required: true,
      tooltip: t('setting.slackBotTokenTip'),
    },
    {
      label: t('setting.connectorFields.channels'),
      name: 'config.channels',
      type: FormFieldType.Tag,
      required: false,
      tooltip: t('setting.slackChannelsTip'),
    },
  ],
  [DataSourceKey.SHAREPOINT]: [
    {
      label: t('setting.connectorFields.siteUrl'),
      name: 'config.credentials.site_url',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'https://contoso.sharepoint.com/sites/MySite',
      tooltip: t('setting.sharepointSiteUrlTip'),
    },
    {
      label: t('setting.connectorFields.tenantId'),
      name: 'config.credentials.tenant_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.clientId'),
      name: 'config.credentials.client_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.clientSecret'),
      name: 'config.credentials.client_secret',
      type: FormFieldType.Password,
      required: true,
    },
  ],
  [DataSourceKey.JIRA]: jiraConstant(t),
  [DataSourceKey.WEBDAV]: [
    {
      label: t('setting.connectorFields.webdavServerUrl'),
      name: 'config.base_url',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'https://webdav.example.com',
    },
    {
      label: t('setting.connectorFields.username'),
      name: 'config.credentials.username',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.password'),
      name: 'config.credentials.password',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.remotePath'),
      name: 'config.remote_path',
      type: FormFieldType.Text,
      required: false,
      placeholder: '/',
      tooltip: t('setting.webdavRemotePathTip'),
    },
  ],
  [DataSourceKey.DROPBOX]: [
    {
      label: t('setting.connectorFields.accessToken'),
      name: 'config.credentials.dropbox_access_token',
      type: FormFieldType.Password,
      required: true,
      tooltip: t('setting.dropboxAccessTokenTip'),
    },
    {
      label: t('setting.connectorFields.batchSize'),
      name: 'config.batch_size',
      type: FormFieldType.Number,
      required: false,
      placeholder: t('setting.connectorMessages.defaultsTo2'),
    },
  ],
  [DataSourceKey.BOX]: [
    {
      label: t('setting.connectorFields.boxOauthConfiguration'),
      name: 'config.credentials.box_tokens',
      type: FormFieldType.Textarea,
      required: true,
      render: (fieldProps: any) => (
        <BoxTokenField
          value={fieldProps.value}
          onChange={fieldProps.onChange}
          placeholder='{ "client_id": "...", "client_secret": "...", "redirect_uri": "..." }'
        />
      ),
    },
    {
      label: t('setting.connectorFields.folderId'),
      name: 'config.folder_id',
      type: FormFieldType.Text,
      required: false,
      placeholder: t('setting.connectorMessages.defaultsRoot'),
    },
  ],
  [DataSourceKey.AIRTABLE]: [
    {
      label: t('setting.connectorFields.accessToken'),
      name: 'config.credentials.airtable_access_token',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.baseId'),
      name: 'config.base_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.tableNameOrId'),
      name: 'config.table_name_or_id',
      type: FormFieldType.Text,
      required: true,
    },
  ],
  [DataSourceKey.DINGTALK_AI_TABLE]: [
    {
      label: t('setting.connectorFields.accessToken'),
      name: 'config.credentials.access_token',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.baseId'),
      name: 'config.table_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.operatorId'),
      name: 'config.operator_id',
      type: FormFieldType.Text,
      required: true,
    },
  ],
  [DataSourceKey.GITLAB]: [
    {
      label: t('setting.connectorFields.projectOwner'),
      name: 'config.project_owner',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.projectName'),
      name: 'config.project_name',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.gitlabPersonalAccessToken'),
      name: 'config.credentials.gitlab_access_token',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.gitlabUrl'),
      name: 'config.gitlab_url',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'https://gitlab.com',
    },
    {
      label: t('setting.connectorFields.includeMergeRequests'),
      name: 'config.include_mrs',
      type: FormFieldType.Checkbox,
      required: false,
      defaultValue: true,
    },
    {
      label: t('setting.connectorFields.includeIssues'),
      name: 'config.include_issues',
      type: FormFieldType.Checkbox,
      required: false,
      defaultValue: true,
    },
    {
      label: t('setting.connectorFields.includeCodeFiles'),
      name: 'config.include_code_files',
      type: FormFieldType.Checkbox,
      required: false,
      defaultValue: true,
    },
  ],
  [DataSourceKey.ASANA]: [
    {
      label: t('setting.connectorFields.apiToken'),
      name: 'config.credentials.asana_api_token_secret',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.workspaceId'),
      name: 'config.asana_workspace_id',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.projectIds'),
      name: 'config.asana_project_ids',
      type: FormFieldType.Text,
      required: false,
    },
    {
      label: t('setting.connectorFields.teamId'),
      name: 'config.asana_team_id',
      type: FormFieldType.Text,
      required: false,
    },
  ],
  [DataSourceKey.GITHUB]: [
    {
      label: t('setting.connectorFields.repositoryOwner'),
      name: 'config.repository_owner',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.repositoryName'),
      name: 'config.repository_name',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.githubAccessToken'),
      name: 'config.credentials.github_access_token',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.includePullRequests'),
      name: 'config.include_pull_requests',
      type: FormFieldType.Checkbox,
      required: false,
      defaultValue: true,
    },
    {
      label: t('setting.connectorMessages.includeIssues'),
      name: 'config.include_issues',
      type: FormFieldType.Checkbox,
      required: false,
      defaultValue: true,
    },
  ],
  [DataSourceKey.IMAP]: [
    {
      label: t('setting.connectorFields.username'),
      name: 'config.credentials.imap_username',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.password'),
      name: 'config.credentials.imap_password',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.host'),
      name: 'config.imap_host',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.port'),
      name: 'config.imap_port',
      type: FormFieldType.Number,
      required: true,
    },
    {
      label: t('setting.connectorFields.mailboxes'),
      name: 'config.imap_mailbox',
      type: FormFieldType.Tag,
      required: false,
    },
    {
      label: t('setting.connectorFields.pollRange'),
      name: 'config.poll_range',
      type: FormFieldType.Number,
      required: false,
    },
  ],
  [DataSourceKey.BITBUCKET]: bitbucketConstant(t),
  [DataSourceKey.ZENDESK]: [
    {
      label: t('setting.connectorFields.zendeskDomain'),
      name: 'config.credentials.zendesk_subdomain',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.zendeskEmail'),
      name: 'config.credentials.zendesk_email',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.zendeskToken'),
      name: 'config.credentials.zendesk_token',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.content'),
      name: 'config.zendesk_content_type',
      type: FormFieldType.Segmented,
      required: true,
      options: [
        { label: t('setting.connectorFields.articles'), value: 'articles' },
        { label: t('setting.connectorFields.tickets'), value: 'tickets' },
      ],
    },
  ],
  [DataSourceKey.SEAFILE]: seafileConstant(t),
  [DataSourceKey.MYSQL]: [
    {
      label: t('setting.connectorFields.host'),
      name: 'config.host',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'localhost',
    },
    {
      label: t('setting.connectorFields.port'),
      name: 'config.port',
      type: FormFieldType.Number,
      required: true,
      placeholder: '3306',
    },
    {
      label: t('setting.connectorFields.database'),
      name: 'config.database',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.username'),
      name: 'config.credentials.username',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.password'),
      name: 'config.credentials.password',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.sqlQuery'),
      name: 'config.query',
      type: FormFieldType.Textarea,
      required: false,
      placeholder: t('setting.connectorMessages.leaveEmptyToLoadAllTables'),
      tooltip: t('setting.mysqlQueryTip'),
    },
    {
      label: t('setting.connectorFields.contentColumns'),
      name: 'config.content_columns',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'title,description,content',
      tooltip: t('setting.mysqlContentColumnsTip'),
    },
    {
      label: t('setting.connectorFields.metadataColumns'),
      name: 'config.metadata_columns',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'id,category,status',
      tooltip: t('setting.mysqlMetadataColumnsTip'),
    },
    {
      label: t('setting.connectorFields.idColumn'),
      name: 'config.id_column',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'id',
      tooltip: t('setting.mysqlIdColumnTip'),
    },
    {
      label: t('setting.connectorFields.timestampColumn'),
      name: 'config.timestamp_column',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'updated_at',
      tooltip: t('setting.mysqlTimestampColumnTip'),
    },
  ],
  [DataSourceKey.POSTGRESQL]: [
    {
      label: t('setting.connectorFields.host'),
      name: 'config.host',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'localhost',
    },
    {
      label: t('setting.connectorFields.port'),
      name: 'config.port',
      type: FormFieldType.Number,
      required: true,
      placeholder: '5432',
    },
    {
      label: t('setting.connectorFields.database'),
      name: 'config.database',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.username'),
      name: 'config.credentials.username',
      type: FormFieldType.Text,
      required: true,
    },
    {
      label: t('setting.connectorFields.password'),
      name: 'config.credentials.password',
      type: FormFieldType.Password,
      required: true,
    },
    {
      label: t('setting.connectorFields.sqlQuery'),
      name: 'config.query',
      type: FormFieldType.Textarea,
      required: false,
      placeholder: t('setting.connectorMessages.leaveEmptyToLoadAllTables'),
      tooltip: t('setting.postgresqlQueryTip'),
    },
    {
      label: t('setting.connectorFields.contentColumns'),
      name: 'config.content_columns',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'title,description,content',
      tooltip: t('setting.postgresqlContentColumnsTip'),
    },
    {
      label: t('setting.connectorFields.metadataColumns'),
      name: 'config.metadata_columns',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'id,category,status',
      tooltip: t('setting.postgresqlMetadataColumnsTip'),
    },
    {
      label: t('setting.connectorFields.idColumn'),
      name: 'config.id_column',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'id',
      tooltip: t('setting.postgresqlIdColumnTip'),
    },
    {
      label: t('setting.connectorFields.timestampColumn'),
      name: 'config.timestamp_column',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'updated_at',
      tooltip: t('setting.postgresqlTimestampColumnTip'),
    },
  ],
  [DataSourceKey.BIGQUERY]: [
    {
      label: t('setting.connectorFields.projectId'),
      name: 'config.project_id',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'my-gcp-project',
      tooltip: t('setting.bigqueryProjectIdTip'),
    },
    {
      label: t('setting.connectorFields.location'),
      name: 'config.location',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'US',
      tooltip: t('setting.bigqueryLocationTip'),
    },
    {
      label: t('setting.connectorFields.serviceAccountJson'),
      name: 'config.credentials.service_account_json',
      type: FormFieldType.Password,
      required: true,
      placeholder: '{ "type": "service_account", "project_id": "...", ... }',
      tooltip: t('setting.bigqueryServiceAccountJsonTip'),
    },
    {
      label: t('setting.connectorFields.datasetId'),
      name: 'config.dataset_id',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'analytics',
      tooltip: t('setting.bigqueryDatasetIdTip'),
      customValidate: (val: string, values: any) => {
        const hasQuery = !!(values?.config?.query ?? '').trim();
        const hasTable = !!(values?.config?.table_id ?? '').trim();
        if (!hasQuery && !((val ?? '').trim() && hasTable)) {
          return t(
            'setting.connectorMessages.datasetIdIsRequiredWhenNotUsingACustomSqlQuery',
          );
        }
        return true;
      },
    },
    {
      label: t('setting.connectorFields.tableId'),
      name: 'config.table_id',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'customers',
      tooltip: t('setting.bigqueryTableIdTip'),
      customValidate: (val: string, values: any) => {
        const hasQuery = !!(values?.config?.query ?? '').trim();
        const hasDataset = !!(values?.config?.dataset_id ?? '').trim();
        if (!hasQuery && !(hasDataset && (val ?? '').trim())) {
          return t(
            'setting.connectorMessages.tableIdIsRequiredWhenNotUsingACustomSqlQuery',
          );
        }
        return true;
      },
    },
    {
      label: t('setting.connectorFields.sqlQuery'),
      name: 'config.query',
      type: FormFieldType.Textarea,
      required: false,
      placeholder: t(
        'setting.connectorMessages.leaveEmptyToUseDatasetIdTableId',
      ),
      tooltip: t('setting.bigqueryQueryTip'),
      customValidate: (val: string, values: any) => {
        const hasQuery = !!(val ?? '').trim();
        const hasDataset = !!(values?.config?.dataset_id ?? '').trim();
        const hasTable = !!(values?.config?.table_id ?? '').trim();
        if (!hasQuery && !(hasDataset && hasTable)) {
          return t(
            'setting.connectorMessages.provideASqlQueryOrBothDatasetIdAndTableId',
          );
        }
        return true;
      },
    },
    {
      label: t('setting.connectorFields.contentColumns'),
      name: 'config.content_columns',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'name,description,notes',
      tooltip: t('setting.bigqueryContentColumnsTip'),
    },
    {
      label: t('setting.connectorFields.metadataColumns'),
      name: 'config.metadata_columns',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'customer_id,status,region',
      tooltip: t('setting.bigqueryMetadataColumnsTip'),
    },
    {
      label: t('setting.connectorFields.idColumn'),
      name: 'config.id_column',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'customer_id',
      tooltip: t('setting.bigqueryIdColumnTip'),
    },
    {
      label: t('setting.connectorFields.timestampColumn'),
      name: 'config.timestamp_column',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'updated_at',
      tooltip: t('setting.bigqueryTimestampColumnTip'),
    },
    {
      label: t('setting.connectorFields.maxBytesBilled'),
      name: 'config.maximum_bytes_billed',
      type: FormFieldType.Number,
      required: false,
      placeholder: '1073741824',
      tooltip: t('setting.bigqueryMaximumBytesBilledTip'),
      validation: {
        min: 1,
        message: t('setting.connectorMessages.maxBytesBilledMustBeAtLeast1'),
      },
    },
    {
      label: t('setting.connectorFields.jobTimeoutMs'),
      name: 'config.job_timeout_ms',
      type: FormFieldType.Number,
      required: false,
      placeholder: '300000',
      tooltip: t('setting.bigqueryJobTimeoutMsTip'),
      validation: {
        min: 1,
        message: t('setting.connectorMessages.jobTimeoutMustBeAtLeast1'),
      },
    },
    {
      label: t('setting.connectorFields.pageSize'),
      name: 'config.page_size',
      type: FormFieldType.Number,
      required: false,
      placeholder: '1000',
      validation: {
        min: 1,
        message: t('setting.connectorMessages.pageSizeMustBeAtLeast1'),
      },
    },
    {
      label: t('setting.connectorFields.batchSize'),
      name: 'config.batch_size',
      type: FormFieldType.Number,
      required: false,
      placeholder: '100',
      validation: {
        min: 1,
        message: t('setting.connectorMessages.batchSizeMustBeAtLeast1'),
      },
    },
    {
      label: t('setting.connectorFields.useQueryCache'),
      name: 'config.use_query_cache',
      type: FormFieldType.Checkbox,
      required: false,
      defaultValue: true,
    },
  ],
  [DataSourceKey.REST_API]: [
    // ── Essential fields ──────────────────────────────────────────────
    {
      label: t('setting.connectorFields.baseUrl'),
      name: 'config.url',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'https://api.example.com/v1/resources',
    },
    {
      label: t('setting.connectorFields.httpMethod'),
      name: 'config.method',
      type: FormFieldType.Select,
      required: true,
      options: [
        { label: 'GET', value: 'GET' },
        { label: 'POST', value: 'POST' },
      ],
      defaultValue: 'GET',
    },
    {
      label: t('setting.connectorFields.queryParameters'),
      name: 'config.query_params',
      type: FormFieldType.Textarea,
      required: false,
      placeholder: `key=value\none_per_line=true`,
      tooltip: t('setting.restApiQueryParamsTip'),
    },
    {
      label: t('setting.connectorFields.itemsPath'),
      name: 'config.items_path',
      type: FormFieldType.Text,
      required: false,
      placeholder: '$.items',
      tooltip: t('setting.restApiItemsPathTip'),
    },
    {
      label: t('setting.connectorFields.idField'),
      name: 'config.id_field',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'id',
      tooltip: t('setting.restApiIdFieldTip'),
    },
    {
      label: t('setting.connectorFields.authType'),
      name: 'config.auth_type',
      type: FormFieldType.Select,
      required: true,
      options: [
        { label: t('setting.connectorFields.none'), value: 'none' },
        {
          label: t('setting.connectorFields.apiKeyHeader'),
          value: 'api_key_header',
        },
        { label: t('setting.connectorFields.bearerToken'), value: 'bearer' },
        { label: t('setting.connectorFields.basicAuth'), value: 'basic' },
      ],
      defaultValue: 'none',
    },
    {
      label: t('setting.connectorFields.apiKeyHeaderName'),
      name: 'config.auth_config.header_name',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'X-API-Key',
      shouldRender: (values: any) =>
        values?.config?.auth_type === 'api_key_header',
      customValidate: (val: string, values: any) => {
        if (
          values?.config?.auth_type === 'api_key_header' &&
          !(val != null && String(val).trim())
        ) {
          return t('setting.restApiValidationApiKeyHeaderNameRequired');
        }
        return true;
      },
    },
    {
      label: t('setting.connectorFields.apiKeyValue'),
      name: 'config.credentials.api_key',
      type: FormFieldType.Password,
      required: false,
      shouldRender: (values: any) =>
        values?.config?.auth_type === 'api_key_header',
      customValidate: (val: string, values: any) => {
        if (values?.config?.auth_type === 'api_key_header' && !val) {
          return t('setting.restApiValidationApiKeyRequired');
        }
        return true;
      },
    },
    {
      label: t('setting.connectorFields.bearerToken'),
      name: 'config.credentials.token',
      type: FormFieldType.Password,
      required: false,
      shouldRender: (values: any) => values?.config?.auth_type === 'bearer',
      customValidate: (val: string, values: any) => {
        if (values?.config?.auth_type === 'bearer' && !val) {
          return t('setting.restApiValidationBearerTokenRequired');
        }
        return true;
      },
    },
    {
      label: t('setting.connectorFields.username'),
      name: 'config.credentials.username',
      type: FormFieldType.Text,
      required: false,
      shouldRender: (values: any) => values?.config?.auth_type === 'basic',
      customValidate: (val: string, values: any) => {
        if (
          values?.config?.auth_type === 'basic' &&
          !(val != null && String(val).trim())
        ) {
          return t('setting.restApiValidationBasicUsernameRequired');
        }
        return true;
      },
    },
    {
      label: t('setting.connectorFields.password'),
      name: 'config.credentials.password',
      type: FormFieldType.Password,
      required: false,
      shouldRender: (values: any) => values?.config?.auth_type === 'basic',
      customValidate: (val: string, values: any) => {
        if (values?.config?.auth_type === 'basic' && !val) {
          return t('setting.restApiValidationBasicPasswordRequired');
        }
        return true;
      },
    },
    {
      label: t('setting.connectorFields.contentFields'),
      name: 'config.content_fields',
      type: FormFieldType.Text,
      required: true,
      placeholder: 'title,body',
      tooltip: t('setting.restApiContentFieldsTip'),
    },
    {
      label: t('setting.connectorFields.metadataFields'),
      name: 'config.metadata_fields',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'author,category',
      tooltip: t('setting.restApiMetadataFieldsTip'),
    },
    {
      label: t('setting.connectorFields.paginationType'),
      name: 'config.pagination_type',
      type: FormFieldType.Select,
      required: true,
      options: [
        { label: t('setting.connectorFields.none'), value: 'none' },
        { label: t('setting.connectorFields.page'), value: 'page' },
        { label: t('setting.connectorFields.offset'), value: 'offset' },
        { label: t('setting.connectorFields.cursor'), value: 'cursor' },
      ],
      defaultValue: 'none',
    },
    {
      label: t('setting.connectorFields.startPage'),
      name: 'config.pagination_config.start_page',
      type: FormFieldType.Number,
      required: false,
      defaultValue: 1,
      shouldRender: (values: any) => values?.config?.pagination_type === 'page',
    },
    {
      label: t('setting.connectorFields.offsetParam'),
      name: 'config.pagination_config.offset_param',
      type: FormFieldType.Text,
      required: false,
      defaultValue: 'offset',
      shouldRender: (values: any) =>
        values?.config?.pagination_type === 'offset',
    },
    {
      label: t('setting.connectorFields.startOffset'),
      name: 'config.pagination_config.start_offset',
      type: FormFieldType.Number,
      required: false,
      defaultValue: 0,
      shouldRender: (values: any) =>
        values?.config?.pagination_type === 'offset',
    },
    {
      label: t('setting.connectorFields.cursorParam'),
      name: 'config.pagination_config.cursor_param',
      type: FormFieldType.Text,
      required: false,
      defaultValue: 'cursor',
      shouldRender: (values: any) =>
        values?.config?.pagination_type === 'cursor',
    },
    {
      label: t('setting.connectorFields.nextCursorJsonpath'),
      name: 'config.pagination_config.next_cursor_path',
      type: FormFieldType.Text,
      required: false,
      placeholder: '$.next_cursor',
      shouldRender: (values: any) =>
        values?.config?.pagination_type === 'cursor',
      tooltip: t('setting.restApiNextCursorPathTip'),
    },
    // ── Advanced settings toggle ──────────────────────────────────────
    {
      label: t('setting.connectorFields.advancedSettings'),
      name: 'config.show_advanced',
      type: FormFieldType.Switch,
      required: false,
      defaultValue: false,
    },
    // ── Advanced fields (hidden until toggled) ────────────────────────
    {
      label: t('setting.connectorFields.customHeadersJson'),
      name: 'config.headers',
      type: FormFieldType.Textarea,
      required: false,
      placeholder: `{"X-Custom-Header": "value"}`,
      tooltip: t('setting.restApiHeadersTip'),
      shouldRender: (values: any) => !!values?.config?.show_advanced,
    },
    {
      label: t('setting.connectorFields.limitParam'),
      name: 'config.pagination_config.limit_param',
      type: FormFieldType.Text,
      required: false,
      placeholder: t(
        'setting.connectorMessages.limitLeaveEmptyIfAlreadyInQueryParameters',
      ),
      shouldRender: (values: any) =>
        !!values?.config?.show_advanced &&
        values?.config?.pagination_type === 'offset',
    },
    {
      label: t('setting.connectorFields.initialCursor'),
      name: 'config.pagination_config.initial_cursor',
      type: FormFieldType.Text,
      required: false,
      shouldRender: (values: any) =>
        !!values?.config?.show_advanced &&
        values?.config?.pagination_type === 'cursor',
    },
    {
      label: t('setting.connectorFields.maxPages'),
      name: 'config.max_pages',
      type: FormFieldType.Number,
      required: false,
      defaultValue: 1000,
      shouldRender: (values: any) => !!values?.config?.show_advanced,
    },
    {
      label: t('setting.connectorFields.requestDelaySeconds'),
      name: 'config.request_delay',
      type: FormFieldType.Number,
      required: false,
      defaultValue: 0.5,
      placeholder: '0.5',
      tooltip: t('setting.restApiRequestDelayTip'),
      shouldRender: (values: any) => !!values?.config?.show_advanced,
    },
    {
      label: t('setting.connectorFields.pollTimestampField'),
      name: 'config.poll_timestamp_field',
      type: FormFieldType.Text,
      required: false,
      placeholder: 'updated_at',
      tooltip: t('setting.restApiPollTimestampFieldTip'),
      shouldRender: (values: any) => !!values?.config?.show_advanced,
    },
    {
      label: t('setting.connectorFields.requestBodyPostJson'),
      name: 'config.request_body',
      type: FormFieldType.Textarea,
      required: false,
      placeholder: `{"status": "published"}`,
      tooltip: t('setting.restApiRequestBodyTip'),
      shouldRender: (values: any) =>
        !!values?.config?.show_advanced && values?.config?.method === 'POST',
    },
  ],
});

export const DataSourceFormDefaultValues = {
  [DataSourceKey.RSS]: {
    name: '',
    source: DataSourceKey.RSS,
    config: {
      feed_url: '',
      batch_size: 2,
    },
  },
  [DataSourceKey.S3]: {
    name: '',
    source: DataSourceKey.S3,
    config: {
      bucket_name: '',
      bucket_type: 's3',
      prefix: '',
      credentials: {
        aws_access_key_id: '',
        aws_secret_access_key: '',
        region: '',
        authentication_method: 'access_key',
        aws_role_arn: '',
        endpoint_url: '',
        addressing_style: 'virtual',
      },
    },
  },
  [DataSourceKey.R2]: {
    name: '',
    source: DataSourceKey.R2,
    config: {
      bucket_name: '',
      credentials: {
        account_id: '',
        r2_access_key_id: '',
        r2_secret_access_key: '',
      },
    },
  },
  [DataSourceKey.NOTION]: {
    name: '',
    source: DataSourceKey.NOTION,
    config: {
      root_page_id: '',
      credentials: {
        notion_integration_token: '',
      },
    },
  },
  [DataSourceKey.DISCORD]: {
    name: '',
    source: DataSourceKey.DISCORD,
    config: {
      server_ids: [],
      channels: [],
      credentials: {
        discord_bot_token: '',
      },
    },
  },
  [DataSourceKey.CONFLUENCE]: {
    name: '',
    source: DataSourceKey.CONFLUENCE,
    config: {
      wiki_base: '',
      is_cloud: true,
      space: '',
      page_id: '',
      credentials: {
        confluence_username: '',
        confluence_access_token: '',
      },
      index_mode: 'everything',
    },
  },
  [DataSourceKey.EVA_WIKI]: {
    name: '',
    source: DataSourceKey.EVA_WIKI,
    config: {
      api_base_url: '',
      web_base_url: '',
      project_id: '',
      include_attachments: true,
      include_archived: false,
      verify_ssl: true,
      batch_size: 2,
      attachment_size_limit: 10485760,
      page_size_limit: 26214400,
      retry_count: 3,
      credentials: {
        eva_api_token: '',
      },
    },
  },
  [DataSourceKey.OPENMETADATA]: {
    name: '',
    source: DataSourceKey.OPENMETADATA,
    config: {
      base_url: 'http://host.docker.internal:8585',
      public_url: 'http://127.0.0.1:8585',
      include_columns: true,
      services: [],
      domains: [],
      tags: [],
      batch_size: 20,
      max_entities: 5000,
      timeout_seconds: 12,
      retry_count: 2,
      sync_deleted_files: true,
      credentials: {
        openmetadata_username: '',
        openmetadata_password: '',
        openmetadata_jwt_token: '',
      },
    },
  },
  [DataSourceKey.GOOGLE_DRIVE]: {
    name: '',
    source: DataSourceKey.GOOGLE_DRIVE,
    config: {
      include_shared_drives: false,
      include_my_drives: true,
      include_files_shared_with_me: false,
      allow_images: false,
      shared_drive_urls: '',
      shared_folder_urls: '',
      my_drive_emails: '',
      specific_user_emails: '',
      credentials: {
        google_primary_admin: '',
        google_tokens: '',
        authentication_method: 'uploaded',
      },
    },
  },
  [DataSourceKey.GMAIL]: {
    name: '',
    source: DataSourceKey.GMAIL,
    config: {
      credentials: {
        google_primary_admin: '',
        google_tokens: '',
        authentication_method: 'uploaded',
      },
    },
  },
  [DataSourceKey.GOOGLE_CLOUD_STORAGE]: {
    name: '',
    source: DataSourceKey.GOOGLE_CLOUD_STORAGE,
    config: {
      bucket_name: '',
      credentials: {
        access_key_id: '',
        secret_access_key: '',
      },
    },
  },
  [DataSourceKey.OCI_STORAGE]: {
    name: '',
    source: DataSourceKey.OCI_STORAGE,
    config: {
      bucket_name: '',
      credentials: {
        namespace: '',
        region: '',
        access_key_id: '',
        secret_access_key: '',
      },
    },
  },
  [DataSourceKey.MOODLE]: {
    name: '',
    source: DataSourceKey.MOODLE,
    config: {
      moodle_url: '',
      credentials: {
        moodle_token: '',
      },
    },
  },
  [DataSourceKey.TEAMS]: {
    name: '',
    source: DataSourceKey.TEAMS,
    config: {
      credentials: {
        tenant_id: '',
        client_id: '',
        client_secret: '',
      },
    },
  },
  [DataSourceKey.SLACK]: {
    name: '',
    source: DataSourceKey.SLACK,
    config: {
      channels: [],
      credentials: {
        slack_bot_token: '',
      },
    },
  },
  [DataSourceKey.SHAREPOINT]: {
    name: '',
    source: DataSourceKey.SHAREPOINT,
    config: {
      credentials: {
        site_url: '',
        tenant_id: '',
        client_id: '',
        client_secret: '',
      },
    },
  },
  [DataSourceKey.JIRA]: {
    name: '',
    source: DataSourceKey.JIRA,
    config: {
      is_cloud: true,
      base_url: '',
      project_key: '',
      jql_query: '',
      batch_size: 2,
      include_comments: true,
      include_attachments: false,
      attachment_size_limit: 10 * 1024 * 1024,
      labels_to_skip: [],
      comment_email_blacklist: [],
      scoped_token: false,
      credentials: {
        jira_user_email: '',
        jira_username: '',
        jira_api_token: '',
        jira_password: '',
      },
    },
  },
  [DataSourceKey.WEBDAV]: {
    name: '',
    source: DataSourceKey.WEBDAV,
    config: {
      base_url: '',
      remote_path: '/',
      credentials: {
        username: '',
        password: '',
      },
    },
  },
  [DataSourceKey.DROPBOX]: {
    name: '',
    source: DataSourceKey.DROPBOX,
    config: {
      batch_size: 2,
      credentials: {
        dropbox_access_token: '',
      },
    },
  },
  [DataSourceKey.BOX]: {
    name: '',
    source: DataSourceKey.BOX,
    config: {
      name: '',
      folder_id: '0',
      credentials: {
        box_tokens: '',
      },
    },
  },
  [DataSourceKey.AIRTABLE]: {
    name: '',
    source: DataSourceKey.AIRTABLE,
    config: {
      name: '',
      base_id: '',
      table_name_or_id: '',
      credentials: {
        airtable_access_token: '',
      },
    },
  },
  [DataSourceKey.DINGTALK_AI_TABLE]: {
    name: '',
    source: DataSourceKey.DINGTALK_AI_TABLE,
    config: {
      table_id: '',
      operator_id: '',
      credentials: {
        access_token: '',
      },
    },
  },
  [DataSourceKey.GITLAB]: {
    name: '',
    source: DataSourceKey.GITLAB,
    config: {
      project_owner: '',
      project_name: '',
      gitlab_url: 'https://gitlab.com',
      include_mrs: true,
      include_issues: true,
      include_code_files: true,
      credentials: {
        gitlab_access_token: '',
      },
    },
  },
  [DataSourceKey.ASANA]: {
    name: '',
    source: DataSourceKey.ASANA,
    config: {
      name: '',
      asana_workspace_id: '',
      asana_project_ids: '',
      asana_team_id: '',
      credentials: {
        asana_api_token_secret: '',
      },
    },
  },
  [DataSourceKey.GITHUB]: {
    name: '',
    source: DataSourceKey.GITHUB,
    config: {
      repository_owner: '',
      repository_name: '',
      include_pull_requests: true,
      include_issues: true,
      credentials: {
        github_access_token: '',
      },
    },
  },
  [DataSourceKey.IMAP]: {
    name: '',
    source: DataSourceKey.IMAP,
    config: {
      name: '',
      imap_host: '',
      imap_port: 993,
      imap_mailbox: [],
      poll_range: 30,
      credentials: {
        imap_username: '',
        imap_password: '',
      },
    },
  },
  [DataSourceKey.BITBUCKET]: {
    name: '',
    source: DataSourceKey.BITBUCKET,
    config: {
      workspace: '',
      index_mode: 'workspace',
      repository_slugs: '',
      projects: '',
    },
    credentials: {
      bitbucket_api_token: '',
    },
  },
  [DataSourceKey.ZENDESK]: {
    name: '',
    source: DataSourceKey.ZENDESK,
    config: {
      name: '',
      zendesk_content_type: 'articles',
      credentials: {
        zendesk_subdomain: '',
        zendesk_email: '',
        zendesk_token: '',
      },
    },
  },
  [DataSourceKey.SEAFILE]: {
    name: '',
    source: DataSourceKey.SEAFILE,
    config: {
      seafile_url: '',
      sync_scope: 'account',
      repo_id: '',
      sync_path: '',
      include_shared: true,
      batch_size: 100,
      credentials: {
        seafile_token: '',
        repo_token: '',
      },
    },
  },
  [DataSourceKey.MYSQL]: {
    name: '',
    source: DataSourceKey.MYSQL,
    config: {
      host: 'localhost',
      port: 3306,
      database: '',
      query: '',
      content_columns: '',
      metadata_columns: '',
      id_column: '',
      timestamp_column: '',
      credentials: {
        username: '',
        password: '',
      },
    },
  },
  [DataSourceKey.POSTGRESQL]: {
    name: '',
    source: DataSourceKey.POSTGRESQL,
    config: {
      host: 'localhost',
      port: 5432,
      database: '',
      query: '',
      content_columns: '',
      metadata_columns: '',
      id_column: '',
      timestamp_column: '',
      credentials: {
        username: '',
        password: '',
      },
    },
  },
  [DataSourceKey.BIGQUERY]: {
    name: '',
    source: DataSourceKey.BIGQUERY,
    config: {
      project_id: '',
      dataset_id: '',
      table_id: '',
      location: '',
      query: '',
      content_columns: '',
      metadata_columns: '',
      id_column: '',
      timestamp_column: '',
      batch_size: 100,
      page_size: 1000,
      maximum_bytes_billed: 1073741824,
      job_timeout_ms: 300000,
      use_query_cache: true,
      credentials: {
        service_account_json: '',
      },
    },
  },
  [DataSourceKey.ONEDRIVE]: {
    name: '',
    source: DataSourceKey.ONEDRIVE,
    config: {
      folder_path: '',
      batch_size: 2,
      credentials: {
        tenant_id: '',
        client_id: '',
        client_secret: '',
      },
    },
  },
  [DataSourceKey.OUTLOOK]: {
    name: '',
    source: DataSourceKey.OUTLOOK,
    config: {
      folder: 'inbox',
      user_ids: '',
      batch_size: 2,
      credentials: {
        tenant_id: '',
        client_id: '',
        client_secret: '',
      },
    },
  },
  [DataSourceKey.SALESFORCE]: {
    name: '',
    source: DataSourceKey.SALESFORCE,
    config: {
      objects: '',
      api_version: 'v59.0',
      batch_size: 2,
      credentials: {
        instance_url: '',
        client_id: '',
        client_secret: '',
      },
    },
  },
  [DataSourceKey.AZURE_BLOB]: {
    name: '',
    source: DataSourceKey.AZURE_BLOB,
    config: {
      auth_mode: 'account_key',
      prefix: '',
      batch_size: 2,
      credentials: {
        account_name: '',
        account_key: '',
        connection_string: '',
        container_url: '',
        sas_token: '',
        container_name: '',
      },
    },
  },
  [DataSourceKey.REST_API]: {
    name: '',
    source: DataSourceKey.REST_API,
    config: {
      url: '',
      method: 'GET',
      query_params: '',
      headers: '',
      auth_type: 'none',
      auth_config: {},
      items_path: '',
      id_field: '',
      content_fields: '',
      metadata_fields: '',
      pagination_type: 'none',
      pagination_config: {},
      poll_timestamp_field: '',
      request_body: '',
      max_pages: 1000,
      request_delay: 0.5,
      show_advanced: false,
      credentials: {
        api_key: '',
        token: '',
        username: '',
        password: '',
      },
    },
  },
};

export const getDataSourceFieldsWithExtras = (
  source: DataSourceKey | undefined,
  t: TFunction,
): FormFieldConfig[] => {
  if (!source) {
    return [];
  }

  const definitions = getDataSourceFormFields(t);
  const sourceFields = (definitions[source as keyof typeof definitions] ||
    []) as FormFieldConfig[];
  const extraFields = getCommonExtraFields(source, t);

  if (source !== DataSourceKey.JIRA) {
    return [...sourceFields, ...extraFields];
  }

  const modeFieldIndex = sourceFields.findIndex(
    (field) => field.name === 'config.is_cloud',
  );
  if (modeFieldIndex < 0) {
    return [...sourceFields, ...extraFields];
  }

  const sharedFields = sourceFields.slice(0, modeFieldIndex);
  const modeFields = sourceFields.slice(modeFieldIndex);

  const sharedCheckboxFieldIndex = sharedFields.findIndex(
    (field) => field.type === FormFieldType.Checkbox,
  );

  if (sharedCheckboxFieldIndex < 0) {
    return [...sharedFields, ...extraFields, ...modeFields];
  }

  return [
    ...sharedFields.slice(0, sharedCheckboxFieldIndex),
    ...sharedFields.slice(sharedCheckboxFieldIndex),
    ...extraFields,
    ...modeFields,
  ];
};
