import type { Config } from 'jest';

const config: Config = {
  testEnvironment: 'jsdom',
  transform: {
    '^.+\\.(ts|tsx|js|jsx)$': '<rootDir>/jest-esbuild-transformer.cjs',
  },
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    '^human-id$': '<rootDir>/__mocks__/human-id.js',
    '\\.(css|less|scss|sass)$': '<rootDir>/__mocks__/styleMock.js',
    '\\.(jpg|jpeg|png|gif|svg|webp)$': '<rootDir>/__mocks__/fileMock.js',
  },
  setupFilesAfterEnv: ['<rootDir>/jest-setup.ts'],
  coverageProvider: 'v8',
  collectCoverageFrom: [
    'src/**/*.{ts,tsx,js,jsx}',
    '!src/.umi/**',
    '!src/.umi-test/**',
    '!src/.umi-production/**',
    '!**/*.d.ts',
    '!coverage/**',
    '!dist/**',
    '!config/**',
    '!mock/**',
  ],
  coverageThreshold: {
    global: {
      lines: 8,
      statements: 8,
      functions: 14,
      branches: 47,
    },
  },
  testPathIgnorePatterns: ['/node_modules/', '/dist/'],
  // Render Markdown in component tests using the same ESM parser as the SPA.
  transformIgnorePatterns: [
    '/node_modules/(?!(?:\\.pnpm|@ungap/structured-clone|react-markdown|remark-[^/]+|rehype-raw|parse5|entities|hastscript|web-namespaces|unified|bail|is-plain-obj|trough|vfile(?:-message|-location)?|unist-util-[^/]+|mdast-util-[^/]+|hast-util-[^/]+|micromark[^/]*|decode-named-character-reference|character-entities(?:-legacy|-html4)?|property-information|space-separated-tokens|comma-separated-tokens|estree-util-[^/]+|ccount|escape-string-regexp|trim-lines|devlop|zwitch|html-url-attributes|html-void-elements|longest-streak|markdown-table)/)',
  ],
};

export default config;
