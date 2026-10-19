import { useContext } from 'react';
import { useTranslation as useAppTranslation } from 'react-i18next';
import { de } from '../i18n/locales/de';
import { en } from '../i18n/locales/en';
import { fr } from '../i18n/locales/fr';
import { ru } from '../i18n/locales/ru';
import { tr } from '../i18n/locales/tr';
import { TranslationContext } from '../i18n/translation-context';

const translations = { de, en, fr, ru, tr };

export function useTranslation() {
  const translation = useContext(TranslationContext);
  const { i18n } = useAppTranslation();
  const language = (i18n?.resolvedLanguage || i18n?.language || 'en').split(
    '-',
  )[0];
  return (
    translation ?? translations[language as keyof typeof translations] ?? en
  );
}

export function formatTranslation(
  template: string,
  values: Record<string, string | number>,
) {
  return template.replace(/\{(\w+)\}/g, (_, key) => {
    const value = values[key];
    return value !== undefined ? String(value) : `{${key}}`;
  });
}
