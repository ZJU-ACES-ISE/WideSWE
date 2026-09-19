# Data Construction

This directory contains the reusable scripts used to construct WIDESWE tasks.
Raw API responses, pull-request text, provenance records, manual-review notes,
and intermediate candidate data are intentionally excluded from this release.

The released workflow is:

1. `filter_top200_ecosystems.py` screens multi-repository ecosystems.
2. `mine_indexed_pr_candidates.py` discovers linked, merged pull requests with
   test-file signals in more than one repository.
3. `build_reviewed_case_tasks.py` materializes manually reviewed candidates.
4. `materialize_final_matrix_candidate.py` produces a matrix-ready candidate.
5. `validate_reviewed_case_tasks.py` validates metadata and patch application.

GitHub API access is read from `GITHUB_TOKEN`. No credential is stored in this
repository.

