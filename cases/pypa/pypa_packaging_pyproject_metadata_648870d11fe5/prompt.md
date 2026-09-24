Implement PEP 794 Import Name Metadata support for METADATA 2.5.

Support the `Import-Name` and `Import-Namespace` metadata fields, and the corresponding `project.import-names` and `project.import-namespaces` pyproject fields. Verify that no names match between the two lists, including entries that differ only by the optional `; private` marker. Verify that these are valid Python identifiers, not keywords, dot separated, with an optional `; private` marker. When a dotted import name contains submodules, require its parent namespaces to be present in `project.import-namespaces`.

When either import-name or import-namespace metadata is explicitly set, emit or parse metadata version `2.5`. Preserve the PEP distinction between no entry and an empty `Import-Name` entry; an explicitly empty entry still requires metadata version `2.5`.
