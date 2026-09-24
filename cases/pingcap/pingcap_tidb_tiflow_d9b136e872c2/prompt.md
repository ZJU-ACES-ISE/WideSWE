DM import-into should not pass explicit S3 external ID to TiDB

## Bug Report

When a DM task uses S3 storage with `role-arn` and an explicit `external-id`, Dumpling may need the external ID to assume the AWS role. DM currently reuses the same S3 URI as the Lightning/IMPORT INTO source directory.

For `import-mode: import-into`, older TiDB versions reject an explicit `external-id` in the S3 URI. This means DM cannot use the same URI for both Dumpling and IMPORT INTO in this compatibility scenario.

## Expected behavior

DM should keep the original S3 URI for Dumpling and normal Lightning, but avoid passing explicit `external-id` / `external_id` to TiDB IMPORT INTO so TiDB can handle its own keyspace external ID logic.

This compatibility behavior should be enabled only for import-into mode. Other DM backends should keep it disabled. When the behavior is disabled, external IDs must remain unchanged; non-S3 resource parameters must also remain unchanged. Apply the same external-ID compatibility to S3-compatible resource parameters.
