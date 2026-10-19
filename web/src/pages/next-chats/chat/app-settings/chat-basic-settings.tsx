'use client';

import { AvatarNameDescription } from '@/components/avatar-name-description';
import { KnowledgeBaseFormField } from '@/components/knowledge-base-item';
import { LlmSettingFieldItems } from '@/components/llm-setting-items/next';
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Textarea } from '@/components/ui/textarea';
import { useTranslate } from '@/hooks/common-hooks';
import { prefixName } from '@/utils/form';
import { getDirAttribute } from '@/utils/text-direction';
import { useFormContext, useWatch } from 'react-hook-form';

interface ChatBasicSettingProps {
  prefix?: string;
  option?: Record<string, any>;
  hideName?: boolean;
}

export default function ChatBasicSetting({
  prefix = '',
  hideName = false,
}: ChatBasicSettingProps) {
  const { t } = useTranslate('chat');
  const { t: tPermissions } = useTranslate('flow.settings');
  const form = useFormContext();

  const prologueValue = useWatch({
    control: form.control,
    name: prefixName(prefix, 'prompt_config.prologue'),
  });

  const llmSettingPrefix = prefixName(prefix, 'llm_setting');

  return (
    <div className="space-y-8">
      {hideName || (
        <AvatarNameDescription
          avatarField={prefixName(prefix, 'icon')}
          nameField={prefixName(prefix, 'name')}
          descriptionField={prefixName(prefix, 'description')}
        />
      )}
      <FormField
        control={form.control}
        name={prefixName(prefix, 'permission')}
        render={({ field }) => (
          <FormItem>
            <FormLabel>{tPermissions('permissions')}</FormLabel>
            <FormControl>
              <RadioGroup
                value={field.value || 'me'}
                onValueChange={field.onChange}
                className="flex gap-6"
              >
                {(['me', 'team'] as const).map((permission) => (
                  <FormItem
                    key={permission}
                    className="flex items-center gap-2"
                  >
                    <FormControl>
                      <RadioGroupItem
                        value={permission}
                        id={prefixName(prefix, `permission-${permission}`)}
                      />
                    </FormControl>
                    <FormLabel
                      htmlFor={prefixName(prefix, `permission-${permission}`)}
                      className="!m-0 cursor-pointer font-normal"
                    >
                      {tPermissions(permission)}
                    </FormLabel>
                  </FormItem>
                ))}
              </RadioGroup>
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <LlmSettingFieldItems
        prefix={llmSettingPrefix}
        llmId={prefixName(prefix, 'llm_id')}
        showCollapse
      ></LlmSettingFieldItems>

      <FormField
        control={form.control}
        name={prefixName(prefix, 'prompt_config.prologue')}
        render={({ field }) => (
          <FormItem>
            <FormLabel tooltip={t('setAnOpenerTip')}>
              {t('setAnOpener')}
            </FormLabel>
            <FormControl>
              <Textarea
                {...field}
                dir={getDirAttribute(prologueValue || '')}
              ></Textarea>
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <KnowledgeBaseFormField
        name={prefixName(prefix, 'dataset_ids')}
      ></KnowledgeBaseFormField>
    </div>
  );
}
