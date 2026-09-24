Support querystring form Git URLs for Git build contexts. Today's way of expressing remote Git repositories as contexts does not allow specifying both a commit hash and a branch, and does not distinguish between a tag and a branch.

Add `Build.gitContext()` with `fragment` and `query` formats. Query contexts should carry a normalized `ref` and an optional `checksum`, while retaining the fragment format when Git query forwarding is disabled or unsupported. Boolean environment controls should tolerate unset or invalid values through `parseBoolOrDefault`.

Use the new `gitContext` result for Bake source resolution and build context resolution. Empty source or context inputs and `{{defaultContext}}` templates should resolve from it.
