import { FilterFormField, FormFieldType } from '@/components/dynamic-form';
import { TFunction } from 'i18next';

export const bitbucketConstant = (t: TFunction) => [
  {
    label: t('setting.connectorFields.bitbucketAccountEmail'),
    name: 'config.credentials.bitbucket_account_email',
    type: FormFieldType.Email,
    required: true,
  },
  {
    label: t('setting.connectorFields.bitbucketApiToken'),
    name: 'config.credentials.bitbucket_api_token',
    type: FormFieldType.Password,
    required: true,
  },
  {
    label: t('setting.connectorFields.workspace'),
    name: 'config.workspace',
    type: FormFieldType.Text,
    required: true,
    tooltip: t('setting.bitbucketTopWorkspaceTip'),
  },
  {
    label: t('setting.connectorFields.indexMode'),
    name: 'config.index_mode',
    type: FormFieldType.Segmented,
    options: [
      {
        label: t('setting.connectorFields.repositories'),
        value: 'repositories',
      },
      { label: t('setting.connectorFields.projectS'), value: 'projects' },
      { label: t('setting.connectorFields.workspace'), value: 'workspace' },
    ],
  },
  {
    label: t('setting.connectorFields.repositorySlugs'),
    name: 'config.repository_slugs',
    type: FormFieldType.Text,
    customValidate: (val: string, formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      if (!val && index_mode === 'repositories') {
        return t('setting.connectorMessages.repositorySlugsIsRequired');
      }
      return true;
    },
    shouldRender: (formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      return index_mode === 'repositories';
    },
    tooltip: t('setting.bitbucketRepositorySlugsTip'),
  },
  {
    label: t('setting.connectorFields.projects'),
    name: 'config.projects',
    type: FormFieldType.Text,
    customValidate: (val: string, formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      if (!val && index_mode === 'projects') {
        return t('setting.connectorMessages.projectsIsRequired');
      }
      return true;
    },
    shouldRender: (formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      console.log('formValues.config', formValues?.config);
      return index_mode === 'projects';
    },
    tooltip: t('setting.bitbucketProjectsTip'),
  },
  {
    name: FilterFormField + '.tip',
    label: ' ',
    type: FormFieldType.Custom,
    shouldRender: (formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      return index_mode === 'workspace';
    },
    render: () => (
      <div className="text-sm text-text-secondary bg-bg-card border border-border-button rounded-md px-3 py-2">
        {t('setting.bitbucketWorkspaceTip')}
      </div>
    ),
  },
];
