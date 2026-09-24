Support loading guidelines and skills from installed vendor and npm packages.

Right now, guidelines for all first-party Laravel packages live inside Boost's own `.ai/` directory. Boost is the bottleneck, guidelines go stale, and every new Laravel ecosystem package means more files for the Boost team to maintain.

Add `source` and `path` properties to `Package`, allowing consumers to know where each package was installed and locate its files on disk. Composer and npm packages should expose whether they came from Composer or npm, projects with a relative or absolute custom Composer `vendor-dir` should be handled automatically, and the `source` and `path` fields should be included in `Roster::json()` output. Packages created without scanner metadata should leave both values null; scanner-discovered paths should be absolute, and repeated scans should reflect the current Composer configuration rather than retain an earlier `vendor-dir`.

Use a three-tier resolution system for guidelines: user project overrides in `.ai/guidelines/` first, package-provided files under `vendor/{pkg}/resources/boost/guidelines/` or `node_modules/{pkg}/resources/boost/guidelines/` next, and Boost's built-in `.ai/` directory as the fallback. Load package-provided skills from the matching `resources/boost/skills/` paths; a package skill should override a same-named built-in `.ai/` skill, while the built-in skill remains the fallback when the package provides none.

npm packages from first-party scopes such as `@inertiajs/*` and `@laravel/*`, together with recognized non-scoped first-party packages such as `laravel-echo`, should be treated identically to Composer first-party packages.

Remove the compound `INERTIA` and `WAYFINDER` enum cases in favor of specific variants such as `INERTIA_LARAVEL`, `INERTIA_REACT`, `INERTIA_VUE`, and `INERTIA_SVELTE`. Rename `WAYFINDER_LARAVEL` to `WAYFINDER`, add the `AI` enum case for `laravel/ai`, and update the Inertia guidelines to reference the specific enum variants. Scanning Inertia or Wayfinder packages should produce one specific package entry rather than both a compound and a specific entry.
