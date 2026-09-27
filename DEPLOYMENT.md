# WideSWE project page

The project page lives at the root of the `project-page` branch, separate from
the benchmark's `main` branch. This is a static site with no build dependencies. Open `index.html` directly to
preview it. Manrope headings and Inter body text are self-hosted, with system
fallbacks. Figures and icons are local. Font licenses are in `assets/fonts/`.

The source repository is https://github.com/ZJU-ACES-ISE/WideSWE.
The intended project URL is https://zju-aces-ise.github.io/WideSWE/.
Never push or deploy this page to the anonymous `code-tmp` repository.

## Publication (not performed)

1. Review `assets/wideswe.pdf` and the project-page content.
2. In Settings > Pages > Build and deployment, select **GitHub Actions**.
3. Enable deployments from `project-page` in the `github-pages` environment.
4. Push the reviewed `project-page` branch to the public repository. Do not merge
   into `main`. This starts the **Deploy project page** workflow.

The workflow runs only on pushes to `project-page` or an explicit manual dispatch.
No push or deployment has been performed while preparing this draft.
It stages and uploads only the page files and assets, not benchmark workspaces or symlinks.
No personal access token is required in the website or workflow.

## Maintenance

- Regenerate `tasks.js` with `python3 scripts/build_project_data.py` when updating
  the canonical task index. It validates all linked task directories.
- The result plot is `assets/task_success.png`.
- Interactive bars use the values in the visible Table 1 breakdown. The PNG is
  retained as a no-JavaScript fallback. The case panels summarize Appendix C.1.
- The bundled PDF is the current author-named arXiv preparation copy, not an
  accepted conference version. When an arXiv identifier is available, update
  the paper link and BibTeX. No arXiv ID or publication venue is assumed.
- Icons are from Lucide (ISC license in `assets/LICENSE-lucide`). Model logos
  are from Lobe Icons (`assets/model-logos/LICENSE-LobeIcons`). The interactive
  chart uses Comic Sans MS when installed and bundled Comic Neue otherwise.

Deployment reference:
https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
