module.exports = {
  forbidden: [
    {
      name: 'no-circular-dependencies',
      severity: 'error',
      from: { path: '^src' },
      to: { circular: true },
    },
    {
      name: 'shared-does-not-depend-on-features-or-pages',
      severity: 'error',
      from: {
        path: '^src/(api|components|contexts|hooks|stores|styles|themes|types|utils)/',
      },
      to: { path: '^src/(features|pages)/' },
    },
    {
      name: 'features-do-not-depend-on-pages',
      severity: 'error',
      from: { path: '^src/features/' },
      to: { path: '^src/pages/' },
    },
  ],
  options: {
    doNotFollow: { path: 'node_modules' },
    exclude: { path: '(^|/)(dist|node_modules|coverage)/' },
    tsConfig: { fileName: 'tsconfig.json' },
    tsPreCompilationDeps: true,
    enhancedResolveOptions: {
      conditionNames: ['import', 'require', 'node', 'default'],
      exportsFields: ['exports'],
    },
  },
}
