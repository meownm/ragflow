import { createContext } from 'react';
import type { Translation } from './translation-keys.ts';

export const TranslationContext = createContext<Translation | undefined>(
  undefined,
);
