import { IFlowTemplate } from '@/interfaces/database/agent';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { createInstance } from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { TemplateCard } from './template-card';

const template: IFlowTemplate = {
  id: 'template-id',
  avatar: '',
  canvas_type: 'Recommended',
  create_date: '',
  create_time: 0,
  update_date: '',
  update_time: 0,
  title: { en: 'Research assistant', de: 'Rechercheassistent', zh: '研究助手' },
  description: {
    en: 'Researches a topic and cites sources.',
    de: 'Recherchiert ein Thema und nennt Quellen.',
    zh: '研究主题并引用来源。',
  },
  dsl: {
    components: {},
    history: [],
    globals: {},
    variables: {},
    retrieval: [],
  },
};

async function renderCard(language: string, data = template) {
  const i18n = createInstance();
  await i18n.use(initReactI18next).init({
    lng: language,
    fallbackLng: 'en',
    resources: {
      en: { translation: { flow: { useTemplate: 'Use template' } } },
    },
    interpolation: { escapeValue: false },
  });
  const showModal = jest.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <TemplateCard data={data} showModal={showModal} />
    </I18nextProvider>,
  );
  return { i18n, showModal };
}

describe('TemplateCard localization', () => {
  it.each([
    ['ru', 'en'],
    ['ru-RU', 'en'],
    ['fr', 'en'],
    ['en', 'en'],
    ['en-US', 'en'],
    ['de', 'de'],
    ['de-DE', 'de'],
    ['zh-Hans', 'zh'],
    ['zh-Hant', 'zh'],
  ])('shows readable text for %s using %s', async (language, expected) => {
    await renderCard(language);

    expect(screen.getByText(template.title[expected])).toHaveAttribute(
      'lang',
      expected,
    );
    expect(screen.getByText(template.description[expected])).toHaveAttribute(
      'lang',
      expected,
    );
  });

  it('prefers an exact translation over the base language and English', async () => {
    await renderCard('ru-RU', {
      ...template,
      title: { ...template.title, ru: 'Помощник', 'ru-RU': 'Исследователь' },
      description: { ...template.description, ru: 'Исследует тему.' },
    });

    expect(screen.getByText('Исследователь')).toHaveAttribute('lang', 'ru-RU');
    expect(screen.getByText('Исследует тему.')).toHaveAttribute('lang', 'ru');
  });

  it('falls back independently for missing, empty and whitespace translations', async () => {
    await renderCard('ru-RU', {
      ...template,
      title: { ...template.title, 'ru-RU': '', ru: 'Помощник' },
      description: { ...template.description, ru: '  ' },
    });

    expect(screen.getByText('Помощник')).toHaveAttribute('lang', 'ru');
    expect(screen.getByText(template.description.en)).toHaveAttribute(
      'lang',
      'en',
    );
  });

  it('updates the mounted card when the interface language changes', async () => {
    const { i18n } = await renderCard('ru');
    expect(screen.getByText(template.title.en)).toBeVisible();

    await act(async () => {
      await i18n.changeLanguage('de');
    });

    expect(screen.getByText(template.title.de)).toBeVisible();
    expect(screen.getByText(template.description.de)).toBeVisible();
    expect(screen.queryByText(template.title.en)).not.toBeInTheDocument();
  });

  it('opens the selected template without modifying its data', async () => {
    const { showModal } = await renderCard('ru');
    fireEvent.click(screen.getByRole('button', { name: 'Use template' }));

    expect(showModal).toHaveBeenCalledTimes(1);
    expect(showModal).toHaveBeenCalledWith(template);
    expect(showModal.mock.calls[0][0]).toBe(template);
  });
});
