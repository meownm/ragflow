import SvgIcon from '@/components/svg-icon';
import { useAuth } from '@/hooks/auth-hooks';
import {
  useLogin,
  useLoginChannels,
  useLoginWithChannel,
  useRegister,
} from '@/hooks/use-login-request';
import { SystemConfigKeys, useSystemConfig } from '@/hooks/use-system-request';
import { rsaPsw } from '@/utils';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import {
  useTranslation,
  useTranslation as useUiTranslation,
} from 'react-i18next';
import { useNavigate } from 'react-router';

import { Button, ButtonLoading } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm, UseFormReturn } from 'react-hook-form';
import { z } from 'zod';
import { NICKNAME_PATTERN } from '../user-setting/profile/constants';

type LoginFormContentProps = {
  title: string;
  form: UseFormReturn<any>;
  loading: boolean;
  onCheck: (params: any) => Promise<void>;
  changeTitle: () => void;
  registerEnabled: boolean;
  channels: { channel: string; icon?: string; display_name: string }[];
  handleLoginWithChannel: (channel: string) => void;
  t: ReturnType<typeof useTranslation>['t'];
  disablePasswordLogin?: boolean;
};

function LoginFormContent({
  title,
  form,
  loading,
  onCheck,
  changeTitle,
  registerEnabled,
  channels,
  handleLoginWithChannel,
  t,
  disablePasswordLogin,
}: LoginFormContentProps) {
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const heading = titleRef.current;
    heading?.focus({ preventScroll: true });
    const viewport = heading?.closest('main');
    if (viewport) viewport.scrollTop = 0;
  }, [title]);

  return (
    <div className="w-full" data-testid="auth-card-active">
      <div className="mb-6 space-y-2">
        <h1
          ref={titleRef}
          tabIndex={-1}
          className="text-2xl font-semibold text-text-primary outline-none"
        >
          {title === 'login' ? t('loginTitle') : t('signUpTitle')}
        </h1>
        <p className="text-base text-text-secondary sm:text-sm">
          {title === 'login' ? t('loginDescription') : t('registerDescription')}
        </p>
      </div>
      <div className="rounded-xl border border-border-button bg-bg-component p-4 sm:p-6">
        {!disablePasswordLogin && (
          <Form {...form}>
            <form
              className="flex flex-col gap-5 text-text-primary"
              data-testid="auth-form"
              data-active="true"
              noValidate
              onSubmit={form.handleSubmit(onCheck)}
            >
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel required className="text-base sm:text-sm">
                      {t('emailLabel')}
                    </FormLabel>
                    <FormControl>
                      <Input
                        data-testid="auth-email"
                        type="email"
                        inputMode="email"
                        className="h-12 text-base"
                        placeholder={t('emailPlaceholder')}
                        autoComplete="email"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {title === 'register' && (
                <FormField
                  control={form.control}
                  name="nickname"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel required className="text-base sm:text-sm">
                        {t('nicknameLabel')}
                      </FormLabel>
                      <FormControl>
                        <Input
                          data-testid="auth-nickname"
                          className="h-12 text-base"
                          placeholder={t('nicknamePlaceholder')}
                          autoComplete="username"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel required className="text-base sm:text-sm">
                      {t('passwordLabel')}
                    </FormLabel>
                    <FormControl>
                      <Input
                        data-testid="auth-password"
                        className="h-12 text-base"
                        type={'password'}
                        placeholder={t('passwordPlaceholder')}
                        autoComplete={
                          title === 'login'
                            ? 'current-password'
                            : 'new-password'
                        }
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {title === 'login' && (
                <FormField
                  control={form.control}
                  name="remember"
                  render={({ field }) => (
                    <FormItem>
                      <div className="flex min-h-11 items-center gap-3 group">
                        <FormControl>
                          <Checkbox
                            checked={field.value}
                            onCheckedChange={(checked) => {
                              field.onChange(checked);
                            }}
                            className="size-5 group-hover:border-border-default group-hover:bg-border-button"
                          />
                        </FormControl>
                        <FormLabel className="flex min-h-12 cursor-pointer items-center text-base text-text-secondary sm:min-h-11 sm:text-sm">
                          {t('rememberMe')}
                        </FormLabel>
                      </div>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              <ButtonLoading
                data-testid="auth-submit"
                type="submit"
                loading={loading}
                size="xl"
                className="w-full text-base"
              >
                {title === 'login' ? t('login') : t('continue')}
              </ButtonLoading>
            </form>
          </Form>
        )}

        {title === 'login' && channels && channels.length > 0 && (
          <div
            className={disablePasswordLogin ? 'space-y-3' : 'mt-5 space-y-3'}
          >
            {channels.map((item) => (
              <Button
                variant="outline"
                key={item.channel}
                onClick={() => handleLoginWithChannel(item.channel)}
                className="min-h-12 w-full whitespace-normal text-base"
              >
                <div className="flex items-center">
                  <SvgIcon
                    name={item.icon || 'sso'}
                    width={20}
                    height={20}
                    style={{ marginRight: 5 }}
                  />
                  {t('signInWith', { provider: item.display_name })}
                </div>
              </Button>
            ))}
          </div>
        )}

        {!disablePasswordLogin && title === 'login' && registerEnabled && (
          <div className="mt-5 text-center">
            <p className="flex flex-wrap items-center justify-center gap-x-2 text-base text-text-secondary sm:text-sm">
              {t('signInTip')}
              <Button
                data-testid="auth-toggle-register"
                variant={'transparent'}
                onClick={changeTitle}
                className="min-h-12 px-1 text-base font-medium text-text-primary underline underline-offset-4 hover:bg-transparent sm:min-h-11 sm:text-sm"
              >
                {t('signUp')}
              </Button>
            </p>
          </div>
        )}
        {!disablePasswordLogin && title === 'register' && (
          <div className="mt-5 text-center">
            <p className="flex flex-wrap items-center justify-center gap-x-2 text-base text-text-secondary sm:text-sm">
              {t('signUpTip')}
              <Button
                data-testid="auth-toggle-login"
                variant={'transparent'}
                onClick={changeTitle}
                className="min-h-12 px-1 text-base font-medium text-text-primary underline underline-offset-4 hover:bg-transparent sm:min-h-11 sm:text-sm"
              >
                {t('login')}
              </Button>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

const Login = () => {
  const { t: tUi } = useUiTranslation();
  const [title, setTitle] = useState('login');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { login, loading: signLoading } = useLogin();
  const { register, loading: registerLoading } = useRegister();
  const { channels, loading: channelsLoading } = useLoginChannels();
  const { login: loginWithChannel, loading: loginWithChannelLoading } =
    useLoginWithChannel();
  const { t } = useTranslation('translation', { keyPrefix: 'login' });
  const { t: tSetting } = useTranslation('translation', {
    keyPrefix: 'setting',
  });

  const loading =
    signLoading ||
    registerLoading ||
    channelsLoading ||
    loginWithChannelLoading;
  const { config } = useSystemConfig();
  const registerEnabled = config?.registerEnabled !== 0;

  const { isLogin } = useAuth();
  useEffect(() => {
    if (isLogin) {
      navigate('/');
    }
  }, [isLogin, navigate]);

  const handleLoginWithChannel = async (channel: string) => {
    await loginWithChannel(channel);
  };

  const FormSchema = z
    .object({
      nickname: z.string().optional(),
      email: z
        .string()
        .trim()
        .min(1, { message: t('emailPlaceholder') })
        .email({ message: tUi('common.invalidEmail') }),
      password: z.string().min(1, { message: t('passwordPlaceholder') }),
      remember: z.boolean().optional(),
    })
    .superRefine((data, ctx) => {
      if (title !== 'register') return;
      if (!data.nickname) {
        ctx.addIssue({
          path: ['nickname'],
          message: t('nicknamePlaceholder'),
          code: z.ZodIssueCode.custom,
        });
        return;
      }
      if (!NICKNAME_PATTERN.test(data.nickname)) {
        ctx.addIssue({
          path: ['nickname'],
          message: tSetting('usernameInvalidCharacters'),
          code: z.ZodIssueCode.custom,
        });
      }
    });
  type FormValues = z.infer<typeof FormSchema>;
  const form = useForm<FormValues>({
    defaultValues: {
      nickname: '',
      email: '',
      password: '',
      remember: false,
    },
    resolver: zodResolver(FormSchema),
  });

  const changeTitle = () => {
    if (title === 'login' && !registerEnabled) return;
    form.clearErrors();
    setTitle(title === 'login' ? 'register' : 'login');
  };

  const onCheck = async (params: FormValues) => {
    try {
      const rsaPassWord = rsaPsw(params.password) as string;

      if (title === 'login') {
        const code = await login({
          email: `${params.email}`.trim(),
          password: rsaPassWord,
        });
        if (code === 0) {
          await queryClient.invalidateQueries({
            queryKey: SystemConfigKeys.all,
          });
          navigate('/');
        }
      } else {
        const code = await register({
          nickname: params.nickname ?? '',
          email: params.email ?? '',
          password: rsaPassWord,
        });
        if (code === 0) {
          setTitle('login');
        }
      }
    } catch (errorInfo) {
      console.log('Failed:', errorInfo);
    }
  };

  return (
    <main className="h-full overflow-y-auto bg-bg-base px-4 py-6 text-text-primary sm:py-12">
      <div className="mx-auto flex w-full max-w-[440px] flex-col gap-8">
        <div className="flex items-center gap-3">
          <img
            src="/app-icon.png"
            alt={tUi('common.logo')}
            className="size-10"
          />
          <span className="text-xl font-semibold">Агент Раггер</span>
        </div>
        <LoginFormContent
          title={title}
          form={form}
          loading={loading}
          onCheck={onCheck}
          changeTitle={changeTitle}
          registerEnabled={registerEnabled}
          channels={channels || []}
          handleLoginWithChannel={handleLoginWithChannel}
          t={t}
          disablePasswordLogin={!!config?.disablePasswordLogin}
        />
      </div>
    </main>
  );
};

export default Login;
