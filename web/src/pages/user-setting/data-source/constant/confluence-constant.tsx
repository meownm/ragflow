import { FilterFormField, FormFieldType } from '@/components/dynamic-form';
import { TFunction } from 'i18next';

export const confluenceConstant = (t: TFunction) => [
  {
    label: t('setting.connectorFields.confluenceUsername'),
    name: 'config.credentials.confluence_username',
    type: FormFieldType.Text,
    required: true,
  },
  {
    label: t('setting.connectorFields.confluenceAccessToken'),
    name: 'config.credentials.confluence_access_token',
    type: FormFieldType.Password,
    required: true,
  },
  {
    label: t('setting.connectorFields.wikiBaseUrl'),
    name: 'config.wiki_base',
    type: FormFieldType.Text,
    required: false,
    tooltip: t('setting.confluenceWikiBaseUrlTip'),
  },
  {
    label: t('setting.connectorFields.isCloud'),
    name: 'config.is_cloud',
    type: FormFieldType.Checkbox,
    required: false,
    tooltip: t('setting.confluenceIsCloudTip'),
  },
  {
    label: t('setting.connectorFields.indexMode'),
    name: 'config.index_mode',
    type: FormFieldType.Segmented,
    options: [
      { label: t('setting.connectorFields.everything'), value: 'everything' },
      { label: t('setting.connectorFields.space'), value: 'space' },
      { label: t('setting.connectorFields.page'), value: 'page' },
    ],
  },
  {
    name: 'config.page_id',
    label: t('setting.connectorFields.pageId'),
    type: FormFieldType.Text,
    customValidate: (val: string, formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      console.log('index_mode', index_mode, val);
      if (!val && index_mode === 'page') {
        return t('setting.connectorMessages.pageIdIsRequired');
      }
      return true;
    },
    shouldRender: (formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      return index_mode === 'page';
    },
  },
  {
    name: 'config.space',
    label: t('setting.connectorFields.spaceKey'),
    type: FormFieldType.Text,
    customValidate: (val: string, formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      if (!val && index_mode === 'space') {
        return t('setting.connectorMessages.spaceKeyIsRequired');
      }
      return true;
    },
    shouldRender: (formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      return index_mode === 'space';
    },
  },
  {
    name: 'config.index_recursively',
    label: t('setting.connectorFields.indexRecursively'),
    type: FormFieldType.Checkbox,
    shouldRender: (formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      return index_mode === 'page';
    },
  },
  {
    name: FilterFormField + '.tip',
    label: ' ',
    type: FormFieldType.Custom,
    shouldRender: (formValues: any) => {
      const index_mode = formValues?.config?.index_mode;
      return index_mode === 'everything';
    },
    render: () => (
      <div className="text-sm text-text-secondary bg-bg-card border border-border-button rounded-md px-3 py-2">
        {t(
          'setting.connectorMessages.thisChoiceWillIndexAllPagesTheProvidedCredentialsHaveAccessTo',
        )}
      </div>
    ),
  },
  {
    label: t('setting.connectorFields.spaceKey'),
    name: 'config.space',
    type: FormFieldType.Text,
    required: false,
    hidden: true,
  },
  {
    label: t('setting.connectorFields.pageId'),
    name: 'config.page_id',
    type: FormFieldType.Text,
    required: false,
    hidden: true,
  },
  {
    label: t('setting.connectorFields.indexRecursively'),
    name: 'config.index_recursively',
    type: FormFieldType.Checkbox,
    required: false,
    hidden: true,
  },
];
