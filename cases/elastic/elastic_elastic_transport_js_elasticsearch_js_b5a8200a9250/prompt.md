Update the package so it can be imported both as CommonJS (`require`) and ESM (`import`). The default CommonJS import should continue to work without issue, and full ESM support should be added without dropping existing CommonJS support.

Support dual CommonJS and ESM build outputs with conditional `import` and `require` package exports. The package root and existing public subpaths must resolve in both module formats, with type declarations continuing to resolve correctly. The ESM entry point must expose the same usable public APIs as the CommonJS entry point, including the client or transport classes, connection and pool classes, serializer, errors, and events provided by the package.

Generated ESM artifacts must use valid Node.js module specifiers and must be able to load package metadata and runtime dependencies without module-resolution or circular-initialization errors. Existing CommonJS consumers and subpath imports must remain backward compatible.

Add tests to verify that both ESM and CommonJS imports load successfully, expose the expected public API, and allow core exported classes to be instantiated and used.
