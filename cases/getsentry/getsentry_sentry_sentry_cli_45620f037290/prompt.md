Add preprod snapshot upload support.

Add the initial snapshots POST API. This API is intended to be invoked by the CLI. It accepts an `app_id` and an `images` object keyed by image identifier. Each image includes positive `width` and `height` values and may include metadata such as `file_name`, `dark_mode`, or `device`; an empty `images` object is valid.

The request may include commit comparison metadata such as `head_sha`, `base_sha`, `provider`, `head_repo_name`, `head_ref`, and `pr_number`. Store a manifest for the upload and return `artifactId`, `snapshotMetricsId`, and `imageCount`.

Images are uploaded directly to objectstore from CLI. Add an experimental `sentry-cli build snapshots [OPTIONS] --app-id <APP_ID> <PATH>` command, where `PATH` is the folder containing images to upload.

The objectstore endpoint is currently gated by a feature flag, and only enabled for internal orgs/teams.
