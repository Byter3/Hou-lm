# Publishing the prepared repository

The initial source checkpoint is marked `working-2026-10-15`. It includes the MIT license and public documentation. Runtime behavior is recorded in [the baseline guide](working-baseline.md).

## Before the first push

1. Review `git status --short` and `git ls-files`. The published tree must exclude `.env`, credential exports, HIPs, `.houlm` histories, logs, screenshots, local backups and generated `dist/` archives.
2. Check `git log --format=fuller -1` for the intended author identity. Public commits expose that metadata.
3. Run the tests in [CONTRIBUTING](../CONTRIBUTING.md#run-tests). Record skips and distinguish local checks from hosted CI and live Houdini acceptance.
4. Create an empty GitHub repository with the desired visibility. Do not initialize a separate README or license; this checkout already contains them.

## Push when ready

Replace `OWNER/REPOSITORY` with the repository you created. From this checkout:

```sh
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin HEAD:main
git push origin working-2026-10-15
```

If `origin` already exists, inspect `git remote -v` and use the intended remote instead of adding another one. The first command only configures the remote; the push commands publish the source and baseline tag.

After pushing, verify the public file list and run the configured GitHub Actions workflow. Its Windows/Linux Python jobs skip unavailable Houdini Qt and LiteLLM integration tests; they do not replace interactive validation. Enable private vulnerability reporting if desired so the channel described in `SECURITY.md` is available.

Describe the project as an early Windows/Houdini 20.5.445 release. Do not attach private diagnostic archives or claim a reproducible server deployment from the floating-tag Compose example. A GitHub source download already includes the panel and license; the optional gateway source bundle is generated separately with `python scripts/package_gateway.py`.
