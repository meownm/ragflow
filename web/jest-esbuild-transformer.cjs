const path = require('node:path');
const esbuild = require('esbuild');
const esbuildJest = require('esbuild-jest');

const esbuildJestTransformer = esbuildJest.createTransformer({
  sourcemap: true,
  loaders: { '.ts': 'tsx' },
  jsxFactory: 'React.createElement',
});

const supportedLoaders = ['js', 'jsx', 'ts', 'tsx', 'json'];

module.exports = {
  createTransformer() {
    return {
      process(content, filename, config, opts) {
        const normalizedContent = content
          .replace(/\bimport\.meta\.env\b/g, '({})')
          .replace(/\bimport\.meta\.glob\b/g, 'jestImportMetaGlob');

        if (normalizedContent.includes('ock(')) {
          return esbuildJestTransformer.process(
            normalizedContent,
            filename,
            config,
            opts,
          );
        }

        const extension = path.extname(filename).slice(1);
        const loader =
          extension === 'ts'
            ? 'tsx'
            : supportedLoaders.includes(extension)
              ? extension
              : 'text';
        const result = esbuild.transformSync(normalizedContent, {
          loader,
          format: 'cjs',
          target: 'es2018',
          sourcemap: true,
          sourcesContent: false,
          sourcefile: filename,
          jsxFactory: 'React.createElement',
        });

        return { code: result.code, map: JSON.parse(result.map) };
      },
    };
  },
};
